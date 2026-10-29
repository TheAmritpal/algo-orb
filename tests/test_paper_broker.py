from decimal import Decimal

from app.brokers.base import OrderRequest
from app.brokers.paper import PaperBroker


def order(client_order_id="abc", quantity=1, price=Decimal(10)):
    return OrderRequest(
        client_order_id=client_order_id,
        instrument="NSE_FO|123",
        side="BUY",
        quantity=quantity,
        order_type="LIMIT",
        price=price,
        product="D",
    )


def test_place_get_and_cancel_are_deterministic():
    broker = PaperBroker()
    result = broker.place_order(order())
    assert result.status == "ACCEPTED"
    assert result.broker_order_id == "paper-abc"
    assert broker.get_order("paper-abc").status == "ACCEPTED"
    assert broker.cancel_order("paper-abc").status == "CANCELLED"
    assert broker.get_order("paper-abc").status == "CANCELLED"
