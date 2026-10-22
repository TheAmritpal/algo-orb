"""Regressions found during the final execution review."""

import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from app.core.trading import TradingSettings
from app.repositories.database import get_connection
from app.services.paper_broker import PaperBroker
from app.services.strategy_status import get_strategy_status
from app.strategies.engine import StrategyEngine
from app.strategies.models import BrokerOrder, Fill, OrderIntent, OrderRequest, Quote
from tests.strategy_helpers import FakeMarket, MemoryRepository
from tests.test_strategy_rules import D, at


class PartialBroker(PaperBroker):
    def __init__(self, quote_provider, off_tick=False):
        super().__init__(quote_provider)
        self.off_tick = off_tick

    def _evaluate(self, order):
        request = self.requests[order.broker_id]
        if request.side == "BUY" and order.status not in {"cancelled", "rejected"}:
            fills = (
                (
                    Fill("part1", order.broker_id, 125, D("100")),
                    Fill("part2", order.broker_id, 125, D("100.05")),
                )
                if self.off_tick
                else (Fill("part", order.broker_id, 250, D("100")),)
            )
            result = BrokerOrder(order.broker_id, request.tag, "open", 500, 250, fills)
            self.orders[order.broker_id] = result
            return result
        return super()._evaluate(order)


class ReviewRegressionTests(unittest.TestCase):
    def setup_partial(self, off_tick=False):
        self.repo = MemoryRepository()
        self.market = FakeMarket()
        self.market.q = replace(self.market.q, ask=D("100.05"))
        self.broker = PartialBroker(self.market.quote, off_tick)
        self.engine = StrategyEngine(
            TradingSettings(execution_backend="orders"),
            self.repo,
            self.market,
            self.broker,
            MagicMock(spec=["assert_owned"]),
        )
        self.engine.tick(at())

    def test_off_tick_partial_average_cancels_remainder_and_closes_confirmed_units(
        self,
    ):
        self.setup_partial(True)
        self.engine.tick(at())
        entry = next(i for i in self.repo.intents.values() if i.purpose == "entry")
        self.assertEqual(entry.status, "cancelled")
        self.assertEqual(
            (self.repo.get_run(1).state, self.repo.get_run(1).remaining_quantity),
            ("closed", 0),
        )
        self.assertEqual(
            sum(
                i.request.quantity
                for i in self.repo.intents.values()
                if i.purpose == "exit"
            ),
            250,
        )

    def test_partial_stop_fill_cancels_entry_before_wait_window_ends(self):
        self.setup_partial()
        self.market.move(at(9, 45, 10), "90", "91")
        self.engine.tick(at(9, 45, 10))
        entry = next(i for i in self.repo.intents.values() if i.purpose == "entry")
        self.assertEqual(entry.status, "cancelled")
        self.assertEqual(self.repo.get_run(1).remaining_quantity, 0)

    def test_partial_target_cancels_entry_and_exits_without_waiting_30_seconds(self):
        self.setup_partial()
        self.market.move(at(9, 45, 10), "120", "121")
        self.engine.tick(at(9, 45, 10))
        self.engine.tick(at(9, 45, 10))
        self.assertEqual(
            next(i for i in self.repo.intents.values() if i.purpose == "entry").status,
            "cancelled",
        )
        self.assertEqual(
            (self.repo.get_run(1).state, self.repo.get_run(1).remaining_quantity),
            ("closed", 0),
        )

    def test_triggered_paper_stop_remains_executable_after_gap_rebound_and_restart(
        self,
    ):
        q = Quote("opt", at(), D("88"), D("89"), D("88"))
        broker = PaperBroker(lambda key: q)
        req = OrderRequest("stop", "opt", "D", "SELL", "SL", 500, D("89"), D("90"))
        order = broker.submit(req)
        self.assertEqual(order.status, "open")
        self.assertEqual(order.filled_quantity, 0)
        intent = OrderIntent(
            1, 1, "stop", req, at(), order.broker_id, order.status, list(order.fills)
        )
        q = replace(q, bid=D("95"), ask=D("96"), last=D("95"))
        restarted = PaperBroker(lambda key: q)
        restarted.restore([intent])
        self.assertEqual(restarted.reconcile(intent).filled_quantity, 500)

    def test_paper_does_not_fill_on_stale_quote(self):
        q = Quote("opt", at(), D("99"), D("100"), D("100"))
        broker = PaperBroker(lambda key: q, clock=lambda: at(9, 46))
        req = OrderRequest("entry", "opt", "D", "BUY", "LIMIT", 500, D("100"))
        self.assertEqual(broker.submit(req).filled_quantity, 0)

    def test_database_failure_keeps_live_mode_label(self):
        with (
            patch("app.services.strategy_status.get_broker_readiness"),
            patch(
                "app.services.strategy_status.load_trading_settings",
                return_value=TradingSettings(mode="live", user_id="pinned"),
            ),
            patch(
                "app.services.strategy_status.StrategyRepository",
                side_effect=RuntimeError("secret"),
            ),
        ):
            result = get_strategy_status(now=at())
        self.assertEqual(result.mode, "live")
        self.assertNotIn("secret", repr(result))

    def test_mysql_socket_operations_have_bounded_timeouts(self):
        with (
            patch(
                "app.repositories.database.get_mysql_config",
                return_value={"host": "db"},
            ),
            patch("app.repositories.database.pymysql.connect") as connect,
        ):
            get_connection()
        kwargs = connect.call_args.kwargs
        for name in ("connect_timeout", "read_timeout", "write_timeout"):
            self.assertGreater(kwargs[name], 0)
            self.assertLessEqual(kwargs[name], 5)

    def test_quote_received_after_tick_start_is_checked_at_receive_time(self):
        repo = MemoryRepository()
        market = FakeMarket()
        broker = PaperBroker(market.quote)
        market.underlying = replace(market.underlying, timestamp=at(9, 45, 1))
        market.q = replace(market.q, timestamp=at(9, 45, 1))
        engine = StrategyEngine(
            TradingSettings(execution_backend="orders"),
            repo,
            market,
            broker,
            MagicMock(spec=["assert_owned"]),
            clock=lambda: at(9, 45, 1),
        )
        engine.tick(at())
        self.assertEqual(repo.get_run(1).remaining_quantity, 500)

    def test_run_update_cannot_overwrite_a_newer_worker_heartbeat(self):
        from app.repositories.strategy import _save
        from app.strategies.models import RunKey, StrategyRun

        run = StrategyRun(1, RunKey("app", "u", "reliance_orb", "paper", at().date()))
        cursor = MagicMock()
        cursor.rowcount = 1
        _save(cursor, run, 0)
        statement = cursor.execute.call_args.args[0]
        self.assertNotIn("`heartbeat`=", statement)

    def test_malformed_broker_position_quantity_is_rejected(self):
        from types import SimpleNamespace as N

        from app.services.trading_broker import Broker

        broker = Broker(MagicMock())
        broker.portfolio = MagicMock()
        broker.portfolio.get_positions.return_value = N(
            status="success",
            data=[N(instrument_token="option", product="D", quantity=None)],
        )
        with self.assertRaises(ValueError):
            broker.positions()

    def test_transient_market_read_failure_can_retry_without_consuming_day(self):
        from app.services.trading_market import TradingUnavailable

        repo = MemoryRepository()
        market = FakeMarket()
        broker = PaperBroker(market.quote)
        engine = StrategyEngine(
            TradingSettings(execution_backend="orders"), repo, market, broker, MagicMock(spec=["assert_owned"])
        )
        original = market.snapshot
        market.snapshot = MagicMock(side_effect=TradingUnavailable("temporary"))
        with self.assertRaises(TradingUnavailable):
            engine.tick(at())
        self.assertFalse(repo.get_run(1).entry_reserved)
        market.snapshot = original
        engine.tick(at())
        self.assertEqual(repo.get_run(1).remaining_quantity, 500)
