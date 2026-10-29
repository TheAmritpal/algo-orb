import pytest

from app.core.errors import UpstoxOAuthError
from app.core.security import sign_state, verify_state
from app.services.upstox_auth import UpstoxAuthService
from tests.fakes import FakeTokenStore, FakeUpstoxSdk


@pytest.fixture
def parts(settings):
    tokens = FakeTokenStore()
    sdk = FakeUpstoxSdk(settings.upstox_account_user_id)
    return UpstoxAuthService(settings, tokens, sdk=sdk), sdk, tokens


def test_login_url_contains_signed_state(parts, settings):
    service, _, _ = parts
    url, state = service.login_url()
    assert "state=" in url
    assert verify_state(settings.app_session_secret, state)["n"]


def test_complete_stores_token_for_configured_account(parts, settings):
    service, _, tokens = parts
    _, state = service.login_url()
    service.complete("code-1", state)
    assert tokens.get(settings.upstox_api_key, settings.upstox_account_user_id) == "token-1"


def test_bad_state_does_not_exchange(parts):
    service, sdk, _ = parts
    with pytest.raises(UpstoxOAuthError):
        service.complete("code-1", "tampered")
    assert sdk.exchanges == []


def test_expired_state_does_not_exchange(parts, settings, clock):
    service, sdk, _ = parts
    state = sign_state(
        settings.app_session_secret,
        {"n": "expired"},
        ttl_seconds=1,
        now=clock.time() - 10,
    )
    with pytest.raises(UpstoxOAuthError):
        service.complete("code-1", state)
    assert sdk.exchanges == []


def test_status_reports_connected_disconnected_unavailable(parts):
    service, sdk, _ = parts
    assert service.status() == "disconnected"
    service.complete("code", service.login_url()[1])
    assert service.status() == "connected"
    sdk.fail = True
    assert service.status() == "unavailable"
