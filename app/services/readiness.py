from __future__ import annotations

from typing import Literal

from app.core.config import Settings
from app.core.errors import BrokerUnavailable
from app.services.upstox_auth import TokenStore


class ReadinessService:
    def __init__(self, settings: Settings, tokens: TokenStore) -> None:
        self.settings = settings
        self.tokens = tokens

    def check(self, mode: Literal["paper", "live"]) -> None:
        if mode == "paper":
            return
        if mode != "live":
            raise BrokerUnavailable("Unknown trading mode")
        if not self.settings.trading_enabled:
            raise BrokerUnavailable("Live trading is disabled")
        if not self.settings.upstox_api_key or not self.settings.upstox_account_user_id:
            raise BrokerUnavailable("Upstox is not configured")
        token = self.tokens.get(
            self.settings.upstox_api_key, self.settings.upstox_account_user_id
        )
        if not token:
            raise BrokerUnavailable("No valid Upstox token")
