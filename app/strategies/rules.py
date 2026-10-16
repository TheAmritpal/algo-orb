"""Pure opening-range, strict-ITM and premium exit decisions."""

import itertools
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.strategies.models import (
    Candle,
    Contract,
    OpeningRange,
    Quote,
    Signal,
    aware,
    money,
)

IST = ZoneInfo("Asia/Kolkata")
UNDERLYING = "NSE_EQ|INE002A01018"


def session_candles(candles: list[Candle], now: datetime) -> list[Candle]:
    aware(now)
    today = now.astimezone(IST).date()
    selected = {}
    for c in candles:
        t = c.start.astimezone(IST)
        if t.date() != today or t.time() < time(9, 15) or t.time() >= time(15, 30):
            continue
        if t.minute % 15 or t.second or t.microsecond:
            raise ValueError("Misaligned candle")
        if c.start in selected and selected[c.start] != c:
            raise ValueError("Conflicting candle")
        selected[c.start] = c
    return sorted(selected.values(), key=lambda c: c.start)


def build_range(candles: list[Candle], now: datetime) -> OpeningRange | None:
    for c in session_candles(candles, now):
        if c.start.astimezone(IST).time() == time(9, 15) and now >= c.start + timedelta(
            minutes=15
        ):
            return OpeningRange(c.high, c.low)
    return None


def breakout(
    candles: list[Candle], opening: OpeningRange, now: datetime, after: datetime | None
) -> Signal | None:
    if now.astimezone(IST).time() > time(15):
        return None
    cs = [
        c
        for c in session_candles(candles, now)
        if c.start + timedelta(minutes=15) <= now
    ]
    if len(cs) < 2 or cs[0].start.astimezone(IST).time() != time(9, 15):
        return None
    if any(
        b.start - a.start != timedelta(minutes=15) for a, b in itertools.pairwise(cs)
    ):
        return None
    c = cs[-1]
    close = c.start + timedelta(minutes=15)
    if not 0 <= (now - close).total_seconds() <= 60 or (after and close <= after):
        return None
    direction = (
        "CE" if c.close > opening.high else "PE" if c.close < opening.low else None
    )
    return Signal(direction, close) if direction else None


def select_contract(
    contracts: list[Contract], spot: Decimal, direction: str, today
) -> Contract:
    money(spot)
    valid = [
        c
        for c in contracts
        if c.underlying_key in {UNDERLYING, "RELIANCE"}
        and not c.weekly
        and c.expiry >= today
    ]
    if not valid:
        raise ValueError("No monthly contracts")
    expiry = min(c.expiry for c in valid)
    candidates = [
        c
        for c in valid
        if c.expiry == expiry
        and c.option_type == direction
        and (c.strike < spot if direction == "CE" else c.strike > spot)
    ]
    if not candidates:
        raise ValueError("No ITM contract")
    return min(candidates, key=lambda c: (abs(c.strike - spot), c.instrument_key))


def premium_levels(average: Decimal, tick: Decimal) -> tuple[Decimal, Decimal]:
    money(average)
    money(tick)
    stop, target = average - 10, average + 20
    if stop <= 0 or stop % tick or target % tick:
        raise ValueError("Unrepresentable premium levels")
    return stop, target


def exit_reason(
    now: datetime, quote: Quote | None, stop: Decimal, target: Decimal
) -> str | None:
    if now.astimezone(IST).time() >= time(15, 25):
        return "time"
    if quote and quote.fresh(now):
        if quote.bid <= stop:
            return "stop"
        if quote.bid >= target:
            return "target"
    return None
