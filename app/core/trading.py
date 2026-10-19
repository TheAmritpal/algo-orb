"""Explicit trading configuration; importing this module never starts trading."""

import os
from dataclasses import dataclass
from decimal import Decimal

from dotenv import load_dotenv

from app.core.config import ENV_FILE
from app.strategies.models import EntryLimits, money


@dataclass(frozen=True)
class TradingSettings:
    mode: str = "paper"
    live_enabled: bool = False
    user_id: str = "paper"
    client_id: str = "paper"
    limits: EntryLimits | None = None
    poll_seconds: int = 2
    quote_max_age_seconds: int = 5
    protective_stop_verified: bool = False
    execution_backend: str = "gtt"


def load_trading_settings() -> TradingSettings:
    load_dotenv(ENV_FILE, override=False)
    mode = os.environ.get("ORB_MODE", "paper")
    enabled = os.environ.get("ORB_LIVE_ENABLED", "false").lower()
    if mode not in {"paper", "live"} or enabled not in {"true", "false"}:
        raise ValueError("Invalid trading mode")
    names = ("PREMIUM", "NOTIONAL", "PLANNED_LOSS", "SPREAD", "SLIPPAGE")
    values = []
    for n in names:
        raw = os.environ.get("ORB_MAX_" + n, "")
        values.append(money(Decimal(raw)) if raw else None)
    limits = EntryLimits(*values) if all(v is not None for v in values) else None
    user = os.environ.get("ORB_BROKER_USER_ID", "").strip()
    client = os.environ.get("UPSTOX_LIVE_API_KEY") or os.environ.get(
        "UPSTOX_API_KEY", "paper"
    )
    poll = int(os.environ.get("ORB_POLL_SECONDS", "2"))
    age = int(os.environ.get("ORB_QUOTE_MAX_AGE_SECONDS", "5"))
    if poll < 2 or not 1 <= age <= 30:
        raise ValueError("Invalid polling/freshness interval")
    if mode == "live" and (enabled != "true" or not user or limits is None):
        raise ValueError(
            "Live trading requires explicit enablement, broker account and monetary limits"
        )
    verified = os.environ.get("ORB_PROTECTIVE_STOP_VERIFIED", "false").lower()
    if verified not in {"true", "false"}:
        raise ValueError("Invalid protective-stop verification flag")
    backend = os.environ.get("ORB_EXECUTION_BACKEND", "gtt")
    if backend not in {"gtt", "orders"}:
        raise ValueError("Invalid execution backend")
    return TradingSettings(
        mode,
        enabled == "true",
        user or "paper",
        client,
        limits,
        poll,
        age,
        verified == "true",
        backend,
    )
