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
    def test_decisions_explain_signal_inside_range_stale_and_missing_candles(self):
        from app.strategies import rules

        self.assertTrue(
            hasattr(rules, "evaluate_breakout"), "Candle decision audit is missing"
        )
        first = candle()
        opening = build_range([first], at())
        cases = [
            ([first, candle(30, "1511")], at(), "signal", "CE"),
            ([first, candle(30, "1489")], at(), "signal", "PE"),
            ([first, candle(30, "1500")], at(), "no_signal", None),
            ([first, candle(30, "1511")], at(9, 46, 1), "expired", "CE"),
            (
                [first, replace(candle(30, "1511"), start=at(10, 0))],
                at(10, 15),
                "invalid_data",
                "CE",
            ),
        ]
        for candles, now, outcome, direction in cases:
            with self.subTest(outcome=outcome, direction=direction):
                decision = rules.evaluate_breakout(candles, opening, now, None)
                self.assertEqual(
                    (decision.outcome, decision.direction), (outcome, direction)
                )
                self.assertEqual(
                    (decision.opening_high, decision.opening_low), (D(1510), D(1490))
                )

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

    def test_october_expiry_rolls_before_restricted_session(self):
        for direction, strike in (("CE", "1480"), ("PE", "1520")):
            october = replace(contract(strike, direction), instrument_key="october")
            november = replace(
                october, instrument_key="november", expiry=date(2026, 11, 23)
            )
            for day, expected in ((23, october), (26, november), (27, november), (28, november)):
                with self.subTest(direction=direction, day=day):
                    self.assertEqual(
                        select_contract(
                            [november, october], D(1500), direction,
                            date(2026, 10, day),
                        ),
                        expected,
                    )

    def test_rollover_uses_next_exchange_session_across_holidays(self):
        october = contract()
        november = replace(october, expiry=date(2026, 11, 23))
        # Monday 23 November expiry is shifted from Tuesday's holiday.
        december = replace(october, expiry=date(2026, 12, 29))
        self.assertEqual(
            select_contract([november, december], D(1500), "CE",
                            date(2026, 11, 20), next_session=date(2026, 11, 23)),
            december,
        )
        # A closed Monday moves the session before Tuesday expiry to Friday.
        self.assertEqual(
            select_contract([october, december], D(1500), "CE",
                            date(2026, 10, 23), next_session=date(2026, 10, 27)),
            december,
        )

    def test_restricted_month_is_not_used_without_later_contracts(self):
        with self.assertRaises(ValueError):
            select_contract([contract()], D(1500), "CE", date(2026, 10, 26))

    def test_expired_month_is_not_reused_when_next_month_is_unavailable(self):
        with self.assertRaises(ValueError):
            select_contract([contract()], D(1500), "CE", date(2026, 10, 28))

    def test_premium_levels_and_time_exit_without_quote(self):
        self.assertEqual(premium_levels(D(19), D("0.05")), (D(14), D(27)))
        self.assertEqual(premium_levels(D("5.05"), D("0.05")), (D("0.05"), D("13.05")))
        for average in [D(5), D(4), D("NaN"), D("100.001")]:
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
