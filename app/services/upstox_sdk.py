from __future__ import annotations

from typing import Protocol
from urllib.parse import urlencode

from upstox_client import ApiClient, Configuration, LoginApi, UserApi
from upstox_client.rest import ApiException

from app.core.config import Settings
from app.core.errors import UpstoxOAuthError

AUTH_BASE_URL = "https://api.upstox.com/v2/login/authorization/dialog"
API_VERSION = "2.0"


class UpstoxSdk(Protocol):
    def authorization_url(self, state: str) -> str: ...

    def exchange_code(self, code: str) -> str: ...

    def profile(self, access_token: str) -> str: ...


class OfficialUpstoxSdk:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def authorization_url(self, state: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.upstox_api_key,
                "redirect_uri": self.settings.upstox_redirect_uri,
                "state": state,
            }
        )
        return f"{AUTH_BASE_URL}?{query}"

    def exchange_code(self, code: str) -> str:
        try:
            client = ApiClient(Configuration())
            response = LoginApi(client).token(
                api_version=API_VERSION,
                code=code,
                client_id=self.settings.upstox_api_key,
                client_secret=self.settings.upstox_api_secret,
                redirect_uri=self.settings.upstox_redirect_uri,
                grant_type="authorization_code",
                _request_timeout=10,
            )
        except (ApiException, OSError, TypeError, ValueError) as exc:
            raise UpstoxOAuthError("Upstox code exchange failed.") from exc
        token = getattr(response, "access_token", None)
        if not token:
            raise UpstoxOAuthError("Upstox returned no access token.")
        return str(token)

    def profile(self, access_token: str) -> str:
        try:
            configuration = Configuration()
            configuration.access_token = access_token
            client = ApiClient(configuration)
            response = UserApi(client).get_profile(API_VERSION, _request_timeout=5)
        except (ApiException, OSError, TypeError, ValueError) as exc:
            raise UpstoxOAuthError("Upstox profile check failed.") from exc
        data = getattr(response, "data", None)
        user_id = getattr(data, "user_id", None)
        if not user_id:
            raise UpstoxOAuthError("Upstox profile has no user id.")
        return str(user_id)
