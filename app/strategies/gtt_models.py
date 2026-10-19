"""GTT parent identity and sanitized broker leg observations."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.strategies.models import OrderRequest, aware, money

LEG_NAMES = {"ENTRY", "TARGET", "STOPLOSS"}
LEG_TERMINAL = {"COMPLETED", "CANCELLED", "EXPIRED", "FAILED"}
LEG_STATES = LEG_TERMINAL | {"SCHEDULED", "TRIGGERED", "OPEN", "PENDING", "INACTIVE"}


@dataclass(frozen=True)
class GttLeg:
    strategy: str
    status: str
    price: Decimal
    side: str
    order_id: str | None = None

    def __post_init__(self):
        money(self.price)
        if self.strategy not in LEG_NAMES or self.status not in LEG_STATES:
            raise ValueError("Unknown GTT leg")
        if self.side != ("BUY" if self.strategy == "ENTRY" else "SELL"):
            raise ValueError("Unexpected GTT side")
        if self.status in {"TRIGGERED", "OPEN", "COMPLETED"} and not self.order_id:
            raise ValueError("GTT child identity missing")


@dataclass(frozen=True)
class GttSnapshot:
    broker_id: str
    legs: tuple[GttLeg, ...]

    def __post_init__(self):
        if len(self.legs) != 3 or {leg.strategy for leg in self.legs} != LEG_NAMES:
            raise ValueError("Incomplete GTT legs")
        ids = [leg.order_id for leg in self.legs if leg.order_id]
        if len(ids) != len(set(ids)):
            raise ValueError("Conflicting GTT children")

    @property
    def terminal(self):
        return all(leg.status in LEG_TERMINAL for leg in self.legs)


@dataclass
class GttRecord:
    run_id: int
    request: OrderRequest
    stop: Decimal
    target: Decimal
    created_at: datetime
    broker_id: str | None = None
    snapshot: GttSnapshot | None = None
    pending: dict | None = field(default=None)

    def __post_init__(self):
        aware(self.created_at)
        money(self.stop)
        money(self.target)
        if self.request.side != "BUY" or not self.stop < self.target:
            raise ValueError("Invalid GTT entry levels")
