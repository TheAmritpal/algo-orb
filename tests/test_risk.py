from decimal import Decimal

import pytest

from app.brokers.base import OrderRequest
from app.core.errors import ValidationError
from app.services.risk import RiskLimits, validate


def order(**overrides):
    values = {
        "client_order_id": "client-1",
        "instrument": "NSE_FO|123",
        "side": "BUY",
        "quantity": 10,
        "order_type": "LIMIT",
        "price": Decimal(100),
        "product": "D",
    }
    values.update(overrides)
    return OrderRequest(**values)


@pytest.fixture
def limits():
    return RiskLimits(max_notional=Decimal(1000), max_quantity=1000)


def test_accepts_limit_order_within_limits(limits):
    validate(order(), limits)


def test_rejects_over_notional(limits):
    with pytest.raises(ValidationError):
        validate(order(quantity=11, price=Decimal(100)), limits)


def test_rejects_over_quantity(limits):
    with pytest.raises(ValidationError):
        validate(order(quantity=1001, price=Decimal(1)), limits)


def test_rejects_zero_quantity_and_missing_price(limits):
    with pytest.raises(ValidationError):
        validate(order(quantity=0), limits)
    with pytest.raises(ValidationError):
        validate(order(price=None), limits)


def test_rejects_market_orders(limits):
    with pytest.raises(ValidationError, match="LIMIT"):
        validate(order(order_type="MARKET"), limits)
