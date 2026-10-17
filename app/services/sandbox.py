"""Sandbox-only SDK order probe. No live fallback or mutation retries."""

import re
import secrets
from dataclasses import dataclass
from decimal import Decimal

import upstox_client as sdk

from app.strategies.models import money, units


@dataclass(frozen=True)
class SandboxOrder:
    instrument: str
    quantity: int
    price: Decimal
    tick_size: Decimal

    def validate(self, *, max_quantity, max_notional):
        units(self.quantity)
        units(max_quantity)
        money(self.price)
        money(self.tick_size)
        money(max_notional)
        if not re.fullmatch(r"NSE_EQ\|[A-Z0-9]{12}", self.instrument):
            raise ValueError("Sandbox probe requires an NSE equity instrument key")
        if self.quantity > max_quantity or self.price * self.quantity > max_notional:
            raise ValueError("Sandbox trading limit exceeded")
        if self.price % self.tick_size:
            raise ValueError("Sandbox price must match the instrument tick size")


def sandbox_client(token):
    configuration = sdk.Configuration(sandbox=True)
    configuration.access_token = token
    client = sdk.ApiClient(configuration)
    client.rest_client.pool_manager.connection_pool_kw["retries"] = False
    return client


def run_order_test(
    request, api, audit, client_id, user_id, *, max_quantity, max_notional
):
    request.validate(max_quantity=max_quantity, max_notional=max_notional)
    body = sdk.PlaceOrderRequest(
        quantity=request.quantity,
        product="D",
        validity="DAY",
        price=float(request.price),
        tag="sb" + secrets.token_hex(8),
        instrument_token=request.instrument,
        order_type="LIMIT",
        transaction_type="BUY",
        disclosed_quantity=0,
        trigger_price=0.0,
        is_amo=False,
    )
    test_id = audit.start(client_id, user_id, body.to_dict())
    # A persisted intent precedes any request. Every ambiguous response stops here.
    try:
        response = api.place_order(body, "2.0", _request_timeout=(3, 10))
        order_id = getattr(getattr(response, "data", None), "order_id", None)
        if (
            getattr(response, "status", None) != "success"
            or not isinstance(order_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", order_id)
        ):
            raise ValueError("Uncertain sandbox acknowledgement")
    except Exception:  # noqa: BLE001 -- broker and database errors may contain credentials  # SDK errors may include tokens; never persist or print them.
        audit.update(test_id, "submission_uncertain", None)
        return "submission_uncertain"
    audit.update(test_id, "place_acknowledged", order_id)
    audit.update(test_id, "cancel_pending", order_id)
    try:
        response = api.cancel_order(order_id, "2.0", _request_timeout=(3, 10))
        if getattr(response, "status", None) != "success":
            raise ValueError("Uncertain sandbox cancellation")
    except Exception:  # noqa: BLE001 -- broker and database errors may contain credentials
        audit.update(test_id, "cancellation_uncertain", order_id)
        return "cancellation_uncertain"
    # A cancellation ACK is not confirmation of a cancelled order or a fill.
    audit.update(test_id, "cancel_acknowledged", order_id)
    return "cancel_acknowledged"


def sandbox_status():
    """Show storage status only: sandbox has no profile verification endpoint."""
    from app.core.config import get_sandbox_config
    from app.repositories.tokens import get_latest_token

    try:
        settings = get_sandbox_config()
        token = get_latest_token(settings["client_id"], environment="sandbox")
        return (
            "Token stored · broker verification requires a sandbox order test."
            if token
            else "No sandbox token stored."
        )
    except Exception:  # noqa: BLE001 -- broker and database errors may contain credentials
        return "Sandbox unavailable. Check app configuration and database migrations."
