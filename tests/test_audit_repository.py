import os
import secrets
from decimal import Decimal

import pytest

from app.brokers.base import OrderRequest
from app.core.config import Settings
from app.repositories.audit import AuditRepository
from tests.conftest import BASE


@pytest.mark.mysql
def test_intent_and_accepted_status_round_trip():
    database = os.environ.get("MYSQL_TEST_DATABASE")
    if not database:
        pytest.skip("MYSQL_TEST_DATABASE is not configured")
    settings = Settings(_env_file=None, **{**BASE, "mysql_database": database})
    repository = AuditRepository(settings)
    client_order_id = f"test-{secrets.token_hex(6)}"
    request = OrderRequest(
        client_order_id=client_order_id,
        instrument="NSE_FO|123",
        side="BUY",
        quantity=1,
        order_type="LIMIT",
        price=Decimal(10),
        product="D",
    )
    repository.create_intent(request, strategy_ref="test")
    assert repository.get(client_order_id).status == "INTENT"
    repository.mark_accepted(client_order_id, "paper-1", "ok")
    record = repository.get(client_order_id)
    assert record.status == "ACCEPTED"
    assert record.broker_order_id == "paper-1"
