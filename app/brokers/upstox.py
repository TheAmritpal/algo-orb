from __future__ import annotations

from decimal import Decimal

from upstox_client import (
    ApiClient,
    Configuration,
    OrderApi,
    PlaceOrderRequest,
)
from upstox_client.rest import ApiException

from app.brokers.base import OrderRequest, OrderResult, OrderSnapshot
from app.core.config import Settings
from app.core.errors import (
    BrokerRejected,
    BrokerUnavailable,
    BrokerUnknown,
)
from app.services.upstox_auth import TokenStore

API_VERSION = "2.0"


def map_api_exception(exc: Exception, *, submitting: bool) -> None:
    if isinstance(exc, ApiException):
        status = exc.status or 0
        if 400 <= status < 500:
            raise BrokerRejected("Upstox rejected the order request.") from exc
        if submitting:
            raise BrokerUnknown("Upstox order state is unknown.") from exc
        raise BrokerUnavailable("Upstox is unavailable.") from exc
    if submitting:
        raise BrokerUnknown("Upstox order state is unknown.") from exc
    raise BrokerUnavailable("Upstox is unavailable.") from exc


class UpstoxBroker:
    def __init__(self, settings: Settings, tokens: TokenStore) -> None:
        self.settings = settings
        self.tokens = tokens

    def _client(self) -> ApiClient:
        token = self.tokens.get(
            self.settings.upstox_api_key, self.settings.upstox_account_user_id
        )
        if not token:
            raise BrokerUnavailable("No valid Upstox token")
        configuration = Configuration()
        configuration.access_token = token
        return ApiClient(configuration)

    def place_order(self, request: OrderRequest) -> OrderResult:
        body = PlaceOrderRequest(
            quantity=request.quantity,
            product=request.product,
            validity="DAY",
            price=float(request.price or Decimal(0)),
            tag=request.client_order_id[:20],
            instrument_token=request.instrument,
            order_type=request.order_type,
            transaction_type=request.side,
            disclosed_quantity=0,
            trigger_price=float(request.trigger_price or 0),
            is_amo=False,
        )
        try:
            response = OrderApi(self._client()).place_order(body, API_VERSION)
        except (ApiException, OSError) as exc:
            map_api_exception(exc, submitting=True)
            raise AssertionError("unreachable") from exc
        data = getattr(response, "data", None)
        order_id = getattr(data, "order_id", None)
        if not order_id:
            raise BrokerUnknown("Upstox returned no order id")
        return OrderResult(broker_order_id=str(order_id), status="ACCEPTED")

    def get_order(self, broker_order_id: str) -> OrderSnapshot:
        try:
            response = OrderApi(self._client()).get_order_details(
                broker_order_id, API_VERSION
            )
        except (ApiException, OSError) as exc:
            map_api_exception(exc, submitting=False)
            raise AssertionError("unreachable") from exc
        data = getattr(response, "data", None)
        return OrderSnapshot(
            broker_order_id=broker_order_id,
            status=str(getattr(data, "status", "unknown")).upper(),
            filled_quantity=int(getattr(data, "filled_quantity", 0) or 0),
            average_price=getattr(data, "average_price", None),
        )

    def cancel_order(self, broker_order_id: str) -> OrderSnapshot:
        try:
            OrderApi(self._client()).cancel_order(broker_order_id, API_VERSION)
        except (ApiException, OSError) as exc:
            map_api_exception(exc, submitting=False)
            raise AssertionError("unreachable") from exc
        return OrderSnapshot(broker_order_id=broker_order_id, status="CANCELLED")
