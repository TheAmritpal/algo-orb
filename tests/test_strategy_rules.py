import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from app.strategies.models import Candle, Contract, Quote
from app.strategies.rules import (
    breakout,
    build_range,
    exit_reason,
    premium_levels,
    select_contract,
)

IST = ZoneInfo("Asia/Kolkata")


def at(h=9, m=45, s=0):
    return datetime(2026, 10, 16, h, m, s, tzinfo=IST)


def candle(m=15, close="1500"):
    return Candle(
        at(9, m),
        D(1500),
        max(D(1510), D(close)),
        min(D(1490), D(close)),
        D(close),
    )


def contract(strike="1480", kind="CE", **kw):
    return Contract(
        "option",
        "RELIANCE",
        date(2026, 10, 27),
        False,
        D(strike),
        kind,
        250,
        D("0.05"),
        **kw,
    )


class RuleTests(unittest.TestCase):
    def test_range_requires_completed_first_candle(self):
        self.assertIsNone(build_range([candle()], at(9, 29, 59)))
        r = build_range([candle()], at(9, 30))
        self.assertEqual((r.high, r.low), (D(1510), D(1490)))

    def test_strict_close_and_earliest_signal(self):
        r = build_range([candle()], at())
        for close, direction in [
            ("1511", "CE"),
            ("1489", "PE"),
            ("1510", None),
            ("1490", None),
            ("1500", None),
        ]:
            with self.subTest(close=close):
                c = replace(candle(30, close), high=D(1520), low=D(1480))
                self.assertIsNone(breakout([candle(), c], r, at(9, 44, 59), None))
                signal = breakout([candle(), c], r, at(), None)
                self.assertEqual(signal.direction if signal else None, direction)

    def test_missing_stale_replayed_and_conflicting_candles(self):
        r = build_range([candle()], at())
        c = replace(candle(30, "1511"), high=D(1520))
        self.assertIsNone(breakout([candle(), c], r, at(9, 46, 1), None))
        self.assertIsNone(breakout([candle(), c], r, at(), at()))
        later = replace(c, start=at(10, 0))
        self.assertIsNone(breakout([candle(), later], r, at(10, 15), None))
        self.assertIsNone(breakout([candle(), c], r, at(15, 0, 1), None))
        self.assertEqual(build_range([candle(), candle()], at()), r)
        with self.assertRaises(ValueError):
            build_range([candle(), replace(candle(), high=D(1520))], at())

    def test_nearest_month_strict_itm_and_contract_filtering(self):
        cs = [
            contract("1480"),
            contract("1500"),
            contract("1520", "PE"),
            contract("1500", "PE"),
            replace(contract("1490"), weekly=True),
            replace(contract("1495"), underlying_key="OTHER"),
            replace(contract("1499"), expiry=date(2026, 9, 1)),
        ]
        self.assertEqual(
            select_contract(cs, D(1500), "CE", date(2026, 10, 16)).strike, D(1480)
        )
        self.assertEqual(
            select_contract(cs, D(1500), "PE", date(2026, 10, 16)).strike, D(1520)
        )
        self.assertEqual(contract().quantity, 500)
        self.assertEqual(replace(contract(), lot_size=500).quantity, 1000)

    def test_premium_levels_and_time_exit_without_quote(self):
        self.assertEqual(premium_levels(D(100), D("0.05")), (D(90), D(118)))
        for average in [D(10), D(9), D("NaN"), D("100.001")]:
            with self.assertRaises(ValueError):
                premium_levels(average, D("0.05"))
        self.assertEqual(exit_reason(at(15, 25), None, D(90), D(120)), "time")
        for bid, reason in [("89", "stop"), ("120", "target"), ("100", None)]:
            q = Quote("option", at(), D(bid), D(bid) + 1, D(bid))
            self.assertEqual(exit_reason(at(), q, D(90), D(120)), reason)

    def test_reject_invalid_market_inputs(self):
        for x in [D("NaN"), D("Infinity"), D(-1)]:
            with self.assertRaises(ValueError):
                Quote("x", at(), x, D(100), D(100))
        with self.assertRaises(ValueError):
            replace(contract(), lot_size=2.5)
        with self.assertRaises(ValueError):
            replace(contract(), tick_size=D(0))
        with self.assertRaises(ValueError):
            replace(candle(), start=datetime(2026, 10, 16))
