from __future__ import annotations

import secrets
from typing import Protocol

from app.core.config import Settings
from app.core.errors import AuthError, UpstoxOAuthError
from app.core.security import sign_state, verify_state
from app.services.upstox_sdk import OfficialUpstoxSdk, UpstoxSdk


class TokenStore(Protocol):
    def get(self, client_id: str, user_id: str) -> str | None: ...

    def upsert(self, client_id: str, user_id: str, access_token: str) -> None: ...

    def delete(self, client_id: str, user_id: str) -> None: ...


class UpstoxAuthService:
    def __init__(
        self,
        settings: Settings,
        tokens: TokenStore,
        sdk: UpstoxSdk | None = None,
    ) -> None:
        self.settings = settings
        self.tokens = tokens
        self.sdk = sdk or OfficialUpstoxSdk(settings)

    @property
    def client_id(self) -> str:
        return self.settings.upstox_api_key

    @property
    def account_id(self) -> str:
        return self.settings.upstox_account_user_id

    def login_url(self) -> tuple[str, str]:
        if not self.client_id or not self.settings.upstox_redirect_uri:
            raise UpstoxOAuthError("Upstox OAuth is not configured.")
        state = sign_state(
            self.settings.app_session_secret,
            {"n": secrets.token_urlsafe(16)},
            ttl_seconds=600,
        )
        return self.sdk.authorization_url(state), state

    def complete(self, code: str, state: str) -> None:
        try:
            verify_state(self.settings.app_session_secret, state)
        except AuthError as exc:
            raise UpstoxOAuthError("Invalid or expired OAuth state.") from exc
        if not code:
            raise UpstoxOAuthError("Missing authorization code.")
        if not self.account_id:
            raise UpstoxOAuthError("UPSTOX_ACCOUNT_USER_ID is not configured.")
        access_token = self.sdk.exchange_code(code)
        profile_user_id = self.sdk.profile(access_token)
        if profile_user_id != self.account_id:
            raise UpstoxOAuthError("Connected Upstox account does not match configuration.")
        self.tokens.upsert(self.client_id, self.account_id, access_token)

    def status(self) -> str:
        if not self.account_id:
            return "unavailable"
        token = self.tokens.get(self.client_id, self.account_id)
        if not token:
            return "disconnected"
        try:
            self.sdk.profile(token)
        except UpstoxOAuthError:
            return "unavailable"
        return "connected"
