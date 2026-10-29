from decimal import Decimal

import pytest

from app.brokers.base import OrderRequest
from app.brokers.paper import PaperBroker
from app.core.errors import BrokerUnavailable, BrokerUnknown, ValidationError
from app.services.order_gateway import OrderGateway
from app.services.risk import RiskLimits
from tests.fakes import FakeAuditStore


class FakeReadiness:
    def __init__(self):
        self.fail = False

    def check(self, mode):
        if self.fail:
            raise BrokerUnavailable("not ready")


class UnknownBroker:
    def __init__(self):
        self.calls = []

    def place_order(self, request):
        self.calls.append(request.client_order_id)
        raise BrokerUnknown("timeout")

    def get_order(self, broker_order_id):
        raise AssertionError

    def cancel_order(self, broker_order_id):
        raise AssertionError


class UnavailableBroker(UnknownBroker):
    def place_order(self, request):
        self.calls.append(request.client_order_id)
        raise BrokerUnavailable("connection lost")


class ExplodingBroker(UnknownBroker):
    def place_order(self, request):
        self.calls.append(request.client_order_id)
        raise RuntimeError("unexpected")


def limit_order(client_order_id="abc", quantity=1, price=None):
    return OrderRequest(
        client_order_id=client_order_id,
        instrument="NSE_FO|123",
        side="BUY",
        quantity=quantity,
        order_type="LIMIT",
        price=price if price is not None else Decimal(10),
        product="D",
    )


def build_gateway(broker=None):
    audit = FakeAuditStore()
    readiness = FakeReadiness()
    gateway = OrderGateway(
        audit=audit,
        broker=broker or PaperBroker(),
        limits=RiskLimits(max_notional=Decimal(1000), max_quantity=1000),
        readiness=readiness,
        mode="paper",
    )
    return gateway, audit, readiness


def test_valid_order_audits_intent_then_accepted():
    gateway, audit, _ = build_gateway()
    result = gateway.submit(limit_order())
    assert result.status == "ACCEPTED"
    assert audit.statuses("abc") == ["INTENT", "ACCEPTED"]


def test_validation_failure_never_audits_or_calls_broker():
    gateway, audit, _ = build_gateway()
    with pytest.raises(ValidationError):
        gateway.submit(limit_order(quantity=0))
    assert audit.rows == {}


def test_not_ready_marks_rejected_without_broker_call():
    gateway, audit, readiness = build_gateway()
    readiness.fail = True
    with pytest.raises(BrokerUnavailable):
        gateway.submit(limit_order())
    assert audit.statuses("abc") == ["INTENT", "REJECTED"]


def test_ambiguous_broker_response_marks_unknown_and_does_not_retry():
    broker = UnknownBroker()
    gateway, audit, _ = build_gateway(broker=broker)
    with pytest.raises(BrokerUnknown):
        gateway.submit(limit_order())
    assert audit.statuses("abc") == ["INTENT", "UNKNOWN"]
    assert broker.calls == ["abc"]


def test_broker_transport_failure_marks_unknown():
    broker = UnavailableBroker()
    gateway, audit, _ = build_gateway(broker=broker)
    with pytest.raises(BrokerUnavailable):
        gateway.submit(limit_order())
    assert audit.statuses("abc") == ["INTENT", "UNKNOWN"]
    assert broker.calls == ["abc"]


def test_audit_failure_prevents_broker_call():
    broker = UnknownBroker()
    gateway, audit, _ = build_gateway(broker=broker)
    audit.fail_create = True
    with pytest.raises(RuntimeError):
        gateway.submit(limit_order())
    assert broker.calls == []


def test_audit_completion_failure_after_acceptance_raises_unknown():
    broker = PaperBroker()
    gateway, audit, _ = build_gateway(broker=broker)
    audit.fail_finish = True
    with pytest.raises(BrokerUnknown):
        gateway.submit(limit_order())
    assert audit.statuses("abc") == ["INTENT"]


def test_unexpected_broker_exception_marks_unknown():
    broker = ExplodingBroker()
    gateway, audit, _ = build_gateway(broker=broker)
    with pytest.raises(BrokerUnknown):
        gateway.submit(limit_order())
    assert audit.statuses("abc") == ["INTENT", "UNKNOWN"]
    assert broker.calls == ["abc"]
