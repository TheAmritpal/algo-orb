from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from upstox_client.rest import ApiException

from app.brokers.base import OrderRequest
from app.brokers.upstox import UpstoxBroker, map_api_exception
from app.core.errors import BrokerRejected, BrokerUnavailable, BrokerUnknown
from tests.fakes import FakeTokenStore


class FakeOrderApi:
    last_body = None

    def __init__(self, client):
        self.client = client

    def place_order(self, body, api_version):
        FakeOrderApi.last_body = body
        return SimpleNamespace(data=SimpleNamespace(order_id="OID-1"))

    def get_order_details(self, order_id, api_version):
        return SimpleNamespace(
            data=SimpleNamespace(status="complete", filled_quantity=5, average_price=11.5)
        )

    def cancel_order(self, order_id, api_version):
        return SimpleNamespace(data=SimpleNamespace(order_id=order_id))


def order():
    return OrderRequest(
        client_order_id="abc",
        instrument="NSE_FO|123",
        side="BUY",
        quantity=1,
        order_type="LIMIT",
        price=Decimal(10),
        product="D",
    )


def test_maps_client_error_to_rejected():
    exc = ApiException(status=400, reason="bad request")
    with pytest.raises(BrokerRejected):
        map_api_exception(exc, submitting=True)


def test_maps_server_error_during_submission_to_unknown():
    exc = ApiException(status=500, reason="server error")
    with pytest.raises(BrokerUnknown):
        map_api_exception(exc, submitting=True)


@pytest.mark.parametrize("status", [408, 409, 429])
def test_maps_ambiguous_client_errors_to_unknown(status):
    exc = ApiException(status=status, reason="ambiguous")
    with pytest.raises(BrokerUnknown):
        map_api_exception(exc, submitting=True)


def test_maps_transport_error_during_read_to_unavailable():
    with pytest.raises(BrokerUnavailable):
        map_api_exception(OSError("connection reset"), submitting=False)


def test_place_get_and_cancel_use_sdk(monkeypatch, settings):
    import app.brokers.upstox as module

    monkeypatch.setattr(module, "OrderApi", FakeOrderApi)
    tokens = FakeTokenStore()
    tokens.upsert(settings.upstox_api_key, settings.upstox_account_user_id, "tok")
    broker = UpstoxBroker(settings, tokens)

    result = broker.place_order(order())
    assert result.broker_order_id == "OID-1"
    assert FakeOrderApi.last_body.instrument_token == "NSE_FO|123"
    assert FakeOrderApi.last_body.tag == "abc"

    snapshot = broker.get_order("OID-1")
    assert snapshot.status == "COMPLETE"
    assert snapshot.filled_quantity == 5
    assert broker.cancel_order("OID-1").status == "CANCELLED"


def test_place_without_order_id_is_unknown(monkeypatch, settings):
    import app.brokers.upstox as module

    class NoIdApi(FakeOrderApi):
        def place_order(self, body, api_version):
            return SimpleNamespace(data=SimpleNamespace(order_id=None))

    monkeypatch.setattr(module, "OrderApi", NoIdApi)
    tokens = FakeTokenStore()
    tokens.upsert(settings.upstox_api_key, settings.upstox_account_user_id, "tok")
    with pytest.raises(BrokerUnknown):
        UpstoxBroker(settings, tokens).place_order(order())
