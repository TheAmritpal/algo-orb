"""Bounded, read-only dashboard probes for the strategy's pinned broker account."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Lock

import upstox_client as sdk
from upstox_client.rest import ApiException

from app.repositories.tokens import get_token
from app.services.trading_broker import Broker
from app.services.trading_market import MarketData, normal_session, sdk_data
from app.strategies.rules import IST, UNDERLYING


@dataclass(frozen=True)
class BrokerReadiness:
    token_state: str = "unknown"
    quote_state: str = "unknown"
    checked_at: datetime | None = None
    quote_at: datetime | None = None
    last_success_at: datetime | None = None

    @property
    def label(self):
        if self.token_state == "valid" and self.quote_state == "fresh":
            return "Broker checks passed"
        if self.token_state == "valid" and self.quote_state == "market_closed":
            return "Market closed · quote freshness not required"
        return "Broker unavailable or not verified"


_gate = Lock()
_checks: dict[tuple[str, str, int], BrokerReadiness] = {}


def get_broker_readiness(settings, *, now=None):
    now_arg_missing = now is None
    now = now or datetime.now(UTC)
    key = (settings.client_id, settings.user_id, settings.quote_max_age_seconds)
    # Serialize probes and reuse results briefly to bound dashboard reload traffic.
    with _gate:
        previous = _checks.get(key)
        if previous and 0 <= (now - previous.checked_at).total_seconds() < 10:
            if (
                previous.quote_state == "fresh"
                and not 0 <= (now - previous.quote_at).total_seconds()
                <= settings.quote_max_age_seconds
            ):
                return replace(previous, quote_state="stale")
            return previous
        result = BrokerReadiness(token_state="unavailable", checked_at=now.astimezone(UTC))
        client = None
        try:
            token = get_token(settings.client_id, settings.user_id)
            if token is None or not token.access_token.strip():
                result = replace(result, token_state="missing")
            else:
                configuration = sdk.Configuration()
                # The SDK constructor can inherit a sandbox configuration default.
                configuration.__init__(sandbox=False)
                configuration.access_token = token.access_token
                client = sdk.ApiClient(configuration)
                profile = sdk.UserApi(client).get_profile("2.0", _request_timeout=(2, 3))
                if (
                    getattr(profile, "status", None) != "success"
                    or getattr(getattr(profile, "data", None), "user_id", None)
                    != settings.user_id
                ):
                    result = replace(result, token_state="invalid")
                else:
                    result = replace(result, token_state="valid", quote_state="unavailable")
                    timings = sdk_data(
                        sdk.MarketHolidaysAndTimingsApi(client).get_exchange_timings,
                        now.astimezone(IST).date().isoformat(),
                    )
                    if not normal_session(timings, now):
                        result = replace(result, quote_state="market_closed")
                    else:
                        quote = MarketData(client).quote(UNDERLYING)
                        received_at = datetime.now(UTC) if now_arg_missing else now
                        fresh = quote.fresh(received_at, settings.quote_max_age_seconds)
                        result = replace(
                            result,
                            quote_state="fresh" if fresh else "stale",
                            quote_at=quote.timestamp.astimezone(UTC),
                        )
        except ApiException as exc:
            if exc.status == 401:
                result = replace(result, token_state="invalid", quote_state="unknown")
        except Exception:  # noqa: BLE001 -- broker/SQL exceptions may contain secrets
            pass
        finally:
            if client is not None:
                Broker(client).close()
        completed_at = datetime.now(UTC) if now_arg_missing else now.astimezone(UTC)
        success = result.token_state == "valid" and result.quote_state == "fresh"
        result = replace(
            result,
            checked_at=completed_at,
            last_success_at=(
                completed_at if success else previous.last_success_at if previous else None
            ),
        )
        # Only a small number of configured accounts are expected in this process.
        if len(_checks) >= 32 and key not in _checks:
            _checks.clear()
        _checks[key] = result
        return result
