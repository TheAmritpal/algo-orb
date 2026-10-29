import pytest

from app.core.errors import BrokerUnavailable
from app.services.readiness import ReadinessService
from tests.fakes import FakeTokenStore


def test_live_disabled_fails(settings):
    tokens = FakeTokenStore()
    with pytest.raises(BrokerUnavailable):
        ReadinessService(settings, tokens).check("live")


def test_live_enabled_requires_token(settings):
    tokens = FakeTokenStore()
    enabled = settings.model_copy(update={"trading_enabled": True})
    with pytest.raises(BrokerUnavailable):
        ReadinessService(enabled, tokens).check("live")
    tokens.upsert(enabled.upstox_api_key, enabled.upstox_account_user_id, "tok")
    ReadinessService(enabled, tokens).check("live")


def test_paper_never_requires_a_token(settings):
    tokens = FakeTokenStore()
    ReadinessService(settings, tokens).check("paper")
