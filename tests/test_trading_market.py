import unittest
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as N
from unittest.mock import MagicMock, patch

from app.services.trading_market import (
    MarketData,
    TradingUnavailable,
    normal_session,
    parse_contract,
    parse_quote,
)
from app.strategies.rules import UNDERLYING
from tests.test_strategy_rules import at


class MarketTests(unittest.TestCase):
    def test_next_trading_session_skips_weekend_and_holiday_and_caches(self):
        market = MarketData(MagicMock())
        market.calendar = MagicMock()

        def timings(day, **kwargs):
            if day in {"2026-10-24", "2026-10-25", "2026-10-26"}:
                return N(status="success", data=[])
            start = at(9, 15).replace(day=27)
            end = at(15, 30).replace(day=27)
            return N(status="success", data=[N(exchange="NSE",
                start_time=int(start.timestamp()*1000),
                end_time=int(end.timestamp()*1000))])

        market.calendar.get_exchange_timings.side_effect = timings
        with patch("app.services.trading_market.clock.sleep"):
            self.assertEqual(market.next_trading_session(at().replace(day=23)),
                             date(2026, 10, 27))
            self.assertEqual(market.next_trading_session(at().replace(day=23)),
                             date(2026, 10, 27))
        self.assertEqual(market.calendar.get_exchange_timings.call_count, 4)

    def test_broker_declared_weekend_session_counts_for_expiry_boundary(self):
        market = MarketData(MagicMock())
        market.calendar = MagicMock()
        start = at(9, 15).replace(day=24)
        end = at(15, 30).replace(day=24)
        market.calendar.get_exchange_timings.return_value = N(
            status="success", data=[N(exchange="NSE",
                start_time=int(start.timestamp()*1000),
                end_time=int(end.timestamp()*1000))]
        )
        with patch("app.services.trading_market.clock.sleep"):
            self.assertEqual(market.next_trading_session(at().replace(day=23)),
                             date(2026, 10, 24))

    def test_missing_future_calendar_blocks_entry_eligibility(self):
        market = MarketData(MagicMock())
        market.calendar = MagicMock()
        market.calendar.get_exchange_timings.return_value = N(status="success", data=[])
        with patch("app.services.trading_market.clock.sleep"), self.assertRaises(TradingUnavailable):
            market.next_trading_session(at())

    def test_contract_identity_paise_tick_and_monthly_expiry(self):
        data = N(
            underlying_key=UNDERLYING,
            underlying_symbol="RELIANCE",
            segment="NSE_FO",
            instrument_key="NSE_FO|123",
            expiry="2026-10-27",
            weekly=False,
            strike_price=1480,
            instrument_type="CE",
            lot_size=250,
            tick_size=5,
        )
        c = parse_contract(data)
        self.assertEqual((c.quantity, c.tick_size), (500, D("0.05")))
        data.underlying_key = "wrong"
        with self.assertRaises(ValueError):
            parse_contract(data)

    def test_quote_uses_feed_timestamp_and_depth_not_last_trade_time(self):
        q = N(
            instrument_token="opt",
            timestamp=str(int(at().timestamp() * 1000)),
            last_price=100,
            depth=N(buy=[N(price=99, quantity=500)], sell=[N(price=100, quantity=500)]),
        )
        result = parse_quote(q, "opt")
        self.assertEqual((result.bid, result.ask), (D(99), D(100)))
        q.instrument_token = "other"
        with self.assertRaises(ValueError):
            parse_quote(q, "opt")

    def test_special_or_missing_sessions_block_entries(self):
        t = lambda h, m: int(at(h, m).timestamp() * 1000)
        self.assertTrue(
            normal_session(
                [N(exchange="NSE", start_time=t(9, 15), end_time=t(15, 30))], at()
            )
        )
        self.assertFalse(
            normal_session(
                [N(exchange="NSE", start_time=t(18, 0), end_time=t(19, 0))], at()
            )
        )
        self.assertFalse(normal_session([], at()))
