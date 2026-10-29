from __future__ import annotations

from typing import Literal

from app.brokers.base import OrderBroker, OrderRequest, OrderResult
from app.core.errors import (
    AppError,
    BrokerRejected,
    BrokerUnavailable,
    BrokerUnknown,
)
from app.services.readiness import ReadinessService
from app.services.risk import RiskLimits, validate


class OrderGateway:
    def __init__(
        self,
        audit,
        broker: OrderBroker,
        limits: RiskLimits,
        readiness: ReadinessService,
        mode: Literal["paper", "live"] = "paper",
    ) -> None:
        self.audit = audit
        self.broker = broker
        self.limits = limits
        self.readiness = readiness
        self.mode = mode

    def submit(
        self, request: OrderRequest, strategy_ref: str | None = None
    ) -> OrderResult:
        validate(request, self.limits)

        self.audit.create_intent(request, strategy_ref)

        try:
            self.readiness.check(self.mode)
        except AppError as exc:
            self.audit.mark_rejected(
                request.client_order_id,
                code=type(exc).__name__,
                message=exc.public_message,
            )
            raise

        try:
            result = self.broker.place_order(request)
        except (BrokerRejected, BrokerUnavailable, BrokerUnknown) as exc:
            if isinstance(exc, BrokerRejected):
                self.audit.mark_rejected(
                    request.client_order_id,
                    code="BROKER_REJECTED",
                    message=exc.public_message,
                )
            else:
                self.audit.mark_unknown(
                    request.client_order_id,
                    code=type(exc).__name__,
                    message=exc.public_message,
                )
            raise

        if result.status == "ACCEPTED":
            self.audit.mark_accepted(
                request.client_order_id, result.broker_order_id, result.message
            )
        elif result.status == "REJECTED":
            self.audit.mark_rejected(
                request.client_order_id,
                code="BROKER_REJECTED",
                message=result.message,
            )
            raise BrokerRejected(result.message or "Order rejected")
        else:
            self.audit.mark_unknown(
                request.client_order_id,
                code="BROKER_UNKNOWN",
                message=result.message,
            )
            raise BrokerUnknown("Broker returned an unknown order state")
        return result
