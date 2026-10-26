"""Whole-session tests: production rules/engine/adapters, SDK transport mocked."""

import unittest
from decimal import Decimal as D
from unittest.mock import MagicMock, patch

import upstox_client as sdk

from app.core.trading import TradingSettings
from app.services.paper_broker import PaperBroker
from app.services.trading_market import MarketData, TradingUnavailable
from app.strategies.engine import StrategyEngine
from app.strategies.rules import UNDERLYING
from tests.strategy_helpers import FakeMarket, MemoryRepository
from tests.test_strategy_rules import at


class SessionTests(unittest.TestCase):
    def test_sdk_model_session_reaches_1525_exit_without_live_mutations(self):
        now = [at()]
        market = MarketData(MagicMock())
        market.options = MagicMock()
        market.history = MagicMock()
        market.calendar = MagicMock()
        market.quotes = MagicMock()
        result = lambda data: sdk.GetOptionContractResponse(status="success", data=data)
        # Use actual SDK response fields, including paise ticks and depth timestamps.
        instrument = sdk.InstrumentData(
            underlying_key=UNDERLYING,
            underlying_symbol="RELIANCE",
            segment="NSE_FO",
            instrument_key="NSE_FO|123",
            expiry=at().replace(day=27),
            weekly=False,
            strike_price=1480,
            instrument_type="CE",
            lot_size=250,
            tick_size=5,
        )
        market.options.get_option_contracts.return_value = result([instrument])
        def exchange_timings(day, **kwargs):
            from datetime import datetime

            selected = datetime.fromisoformat(day).date()
            data = [sdk.ExchangeTimingData(
                exchange="NSE",
                start_time=int(at(9, 15).replace(year=selected.year,
                    month=selected.month, day=selected.day).timestamp()*1000),
                end_time=int(at(15, 30).replace(year=selected.year,
                    month=selected.month, day=selected.day).timestamp()*1000),
            )]
            return type("Response", (), {"status": "success", "data": data})()

        market.calendar.get_exchange_timings.side_effect = exchange_timings
        rows = [
            [at(9, 15).isoformat(), 1500, 1510, 1490, 1500, 1000, 0],
            [at(9, 30).isoformat(), 1500, 1520, 1490, 1511, 1000, 0],
        ]
        market.history.get_intra_day_candle_data.return_value = type(
            "Response",
            (),
            {"status": "success", "data": sdk.IntraDayCandleData(candles=rows)},
        )()

        def quotes(key, *args, **kwargs):
            base = 1500 if key == UNDERLYING else 100
            depth = sdk.DepthMap(
                buy=[sdk.Depth(price=base, quantity=1000)],
                sell=[sdk.Depth(price=base + 0.05, quantity=1000)],
            )
            raw = sdk.MarketQuoteSymbol(
                instrument_token=key,
                timestamp=str(int(now[0].timestamp() * 1000)),
                last_price=base,
                depth=depth,
            )
            return type("Response", (), {"status": "success", "data": {key: raw}})()

        market.quotes.get_full_market_quote.side_effect = quotes
        repo = MemoryRepository()
        broker = PaperBroker(market.quote)
        lock = MagicMock(spec=["assert_owned"])
        engine = StrategyEngine(TradingSettings(execution_backend="orders"), repo, market, broker, lock)
        with patch("app.services.trading_market.clock.sleep"):
            engine.tick(now[0])
            engine.tick(now[0])
            now[0] = at(15, 25)
            engine.tick(now[0])
            engine.tick(now[0])
        run = repo.get_run(1)
        self.assertEqual((run.state, run.remaining_quantity), ("closed", 0))
        self.assertEqual(
            (run.entry_average, run.stop, run.target),
            (D("100.05"), D("95.05"), D("108.05")),
        )
        self.assertTrue(all(i.request.product == "D" for i in repo.intents.values()))
        self.assertEqual(
            len({i.request.tag for i in repo.intents.values()}), len(repo.intents)
        )

    def test_no_breakout_has_no_orders(self):
        repo = MemoryRepository()
        market = FakeMarket()
        market.candles[1] = market.candles[0].__class__(
            at(9, 30), D(1500), D(1520), D(1480), D(1500)
        )
        broker = PaperBroker(market.quote)
        engine = StrategyEngine(
            TradingSettings(execution_backend="orders"), repo, market, broker, MagicMock(spec=["assert_owned"])
        )
        engine.tick(at())
        self.assertEqual(len(repo.intents), 0)

    def test_accepted_entry_timeout_is_found_by_tag_and_not_duplicated(self):
        repo = MemoryRepository()
        market = FakeMarket()
        broker = PaperBroker(market.quote)
        original = broker.submit

        def uncertain(request):
            result = original(request)
            if request.side == "BUY":
                raise TradingUnavailable("uncertain")
            return result

        broker.submit = uncertain
        engine = StrategyEngine(
            TradingSettings(execution_backend="orders"), repo, market, broker, MagicMock(spec=["assert_owned"])
        )
        engine.tick(at())
        engine.tick(at())
        self.assertEqual(repo.get_run(1).remaining_quantity, 500)
        self.assertEqual(sum(i.purpose == "entry" for i in repo.intents.values()), 1)

    def test_delayed_validation_cannot_submit_after_entry_cutoff(self):
        repo = MemoryRepository()
        market = FakeMarket()
        broker = PaperBroker(market.quote)
        engine = StrategyEngine(
            TradingSettings(execution_backend="orders"),
            repo,
            market,
            broker,
            MagicMock(spec=["assert_owned"]),
            clock=MagicMock(side_effect=[at(), at(), at(15, 0, 1)]),
        )
        engine.tick(at())
        self.assertEqual(len(broker.orders), 0)
        self.assertTrue(repo.get_run(1).entry_reserved)
