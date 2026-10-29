from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

Side = Literal["BUY", "SELL"]
OrderType = Literal["LIMIT", "MARKET"]
Product = Literal["I", "D"]
OrderStatus = Literal["ACCEPTED", "REJECTED", "UNKNOWN", "CANCELLED"]


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    instrument: str
    side: Side
    quantity: int
    order_type: OrderType
    price: Decimal | None = None
    trigger_price: Decimal | None = None
    product: Product = "D"


@dataclass(frozen=True)
class OrderResult:
    broker_order_id: str
    status: Literal["ACCEPTED", "REJECTED", "UNKNOWN"]
    message: str | None = None


@dataclass(frozen=True)
class OrderSnapshot:
    broker_order_id: str
    status: str
    filled_quantity: int = 0
    average_price: Decimal | None = None


class OrderBroker(Protocol):
    def place_order(self, request: OrderRequest) -> OrderResult: ...

    def get_order(self, broker_order_id: str) -> OrderSnapshot: ...

    def cancel_order(self, broker_order_id: str) -> OrderSnapshot: ...
