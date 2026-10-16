import unittest
from decimal import Decimal as D
from types import SimpleNamespace as N

from app.services.trading_market import (
    normal_session,
    parse_contract,
    parse_quote,
)
from app.strategies.rules import UNDERLYING
from tests.test_strategy_rules import at


class MarketTests(unittest.TestCase):
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
