"""Validated domain records. Monetary values are finite Decimal amounts."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

Mode = Literal["paper", "live"]
Direction = Literal["CE", "PE"]
Purpose = Literal["entry", "stop", "exit"]
TERMINAL = {"complete", "cancelled", "rejected"}


def money(value: Decimal, positive: bool = True) -> Decimal:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or (value <= 0 if positive else value < 0)
    ):
        raise ValueError("Invalid monetary value")
    return value


def aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone required")


def units(value: int) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError("Positive integer quantity required")


@dataclass(frozen=True)
class Candle:
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self):
        aware(self.start)
        for v in (self.open, self.high, self.low, self.close):
            money(v)
        if (
            not self.low
            <= min(self.open, self.close)
            <= max(self.open, self.close)
            <= self.high
        ):
            raise ValueError("Invalid candle OHLC")


@dataclass(frozen=True)
class OpeningRange:
    high: Decimal
    low: Decimal


@dataclass(frozen=True)
class Signal:
    direction: Direction
    candle_close: datetime


@dataclass(frozen=True)
class Contract:
    instrument_key: str
    underlying_key: str
    expiry: date
    weekly: bool
    strike: Decimal
    option_type: Direction
    lot_size: int
    tick_size: Decimal

    def __post_init__(self):
        units(self.lot_size)
        money(self.strike)
        money(self.tick_size)
        if (
            self.option_type not in {"CE", "PE"}
            or not self.instrument_key
            or not self.underlying_key
        ):
            raise ValueError("Invalid contract")

    @property
    def quantity(self):
        return 2 * self.lot_size


@dataclass(frozen=True)
class Quote:
    instrument_key: str
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    last: Decimal

    def __post_init__(self):
        aware(self.timestamp)
        for v in (self.bid, self.ask, self.last):
            money(v)
        if self.ask < self.bid:
            raise ValueError("Crossed quote")

    def fresh(self, now: datetime, seconds: int = 5) -> bool:
        return 0 <= (now - self.timestamp).total_seconds() <= seconds


@dataclass(frozen=True)
class Fill:
    trade_id: str
    order_id: str
    quantity: int
    price: Decimal

    def __post_init__(self):
        units(self.quantity)
        money(self.price)
        if not self.trade_id or not self.order_id:
            raise ValueError("Missing fill identity")


@dataclass(frozen=True)
class RunKey:
    client_id: str
    user_id: str
    strategy: str
    mode: Mode
    exchange_date: date


@dataclass
class StrategyRun:
    id: int
    key: RunKey
    version: int = 0
    state: str = "waiting_range"
    opening: OpeningRange | None = None
    processed_candle: datetime | None = None
    contract: Contract | None = None
    requested_quantity: int = 0
    filled_quantity: int = 0
    remaining_quantity: int = 0
    entry_average: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    heartbeat: datetime | None = None
    message: str = ""
    entry_reserved: bool = False


@dataclass(frozen=True)
class OrderRequest:
    tag: str
    instrument: str
    product: str
    side: str
    order_type: str
    quantity: int
    price: Decimal
    trigger_price: Decimal = Decimal(0)

    def __post_init__(self):
        units(self.quantity)
        money(self.price)
        money(self.trigger_price, False)
        if (
            self.product != "D"
            or self.side not in {"BUY", "SELL"}
            or self.order_type not in {"LIMIT", "SL"}
        ):
            raise ValueError("Invalid strategy order")
        if not self.instrument or not self.tag or len(self.tag) > 20:
            raise ValueError("Invalid order identity")
        if self.order_type == "SL" and not (
            self.side == "SELL"
            and self.trigger_price >= self.price
            and self.trigger_price > 0
        ):
            raise ValueError("Invalid protective stop")


@dataclass
class OrderIntent:
    id: int
    run_id: int
    purpose: Purpose
    request: OrderRequest
    submitted_at: datetime
    broker_id: str | None = None
    status: str = "unknown"
    fills: list[Fill] = field(default_factory=list)


@dataclass(frozen=True)
class BrokerOrder:
    broker_id: str
    tag: str
    status: str
    requested_quantity: int
    filled_quantity: int
    fills: tuple[Fill, ...] = ()

    def __post_init__(self):
        units(self.requested_quantity)
        if self.status not in TERMINAL | {"open", "trigger_pending", "unknown"}:
            raise ValueError("Unknown broker order status")
        if (
            type(self.filled_quantity) is not int
            or not 0 <= self.filled_quantity <= self.requested_quantity
        ):
            raise ValueError("Invalid broker fill quantity")
        if sum(f.quantity for f in self.fills) != self.filled_quantity:
            raise ValueError("Incomplete broker trade evidence")


@dataclass(frozen=True)
class BrokerPosition:
    instrument: str
    product: str
    quantity: int

    def __post_init__(self):
        if type(self.quantity) is not int or not self.instrument or not self.product:
            raise ValueError("Invalid broker position identity or quantity")


@dataclass(frozen=True)
class EntryLimits:
    max_premium: Decimal
    max_notional: Decimal
    max_planned_loss: Decimal
    max_spread: Decimal
    max_slippage: Decimal


@dataclass(frozen=True)
class MarketSnapshot:
    now: datetime
    normal_session: bool
    candles: list[Candle]
    contracts: list[Contract]
    underlying_quote: Quote | None
    option_quote: Quote | None
