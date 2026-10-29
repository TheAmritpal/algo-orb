from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.brokers.base import OrderRequest
from app.core.errors import ValidationError


@dataclass(frozen=True)
class RiskLimits:
    max_notional: Decimal
    max_quantity: int


def validate(request: OrderRequest, limits: RiskLimits) -> None:
    if not request.client_order_id or len(request.client_order_id) > 64:
        raise ValidationError("client_order_id must be 1-64 characters")
    if not request.instrument.strip():
        raise ValidationError("instrument is required")
    if request.side not in ("BUY", "SELL"):
        raise ValidationError("side must be BUY or SELL")
    if request.product not in ("I", "D"):
        raise ValidationError("product must be I or D")
    if not isinstance(request.quantity, int) or request.quantity <= 0:
        raise ValidationError("quantity must be a positive integer")
    if request.quantity > limits.max_quantity:
        raise ValidationError("quantity exceeds ORDER_MAX_QUANTITY")
    if request.order_type != "LIMIT":
        raise ValidationError("Only LIMIT orders are allowed")
    if request.price is None or request.price <= 0:
        raise ValidationError("price must be positive for a LIMIT order")
    if request.trigger_price is not None and request.trigger_price < 0:
        raise ValidationError("trigger_price cannot be negative")
    notional = Decimal(request.quantity) * request.price
    if notional > limits.max_notional:
        raise ValidationError("order notional exceeds ORDER_MAX_NOTIONAL")
