from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal

from app.brokers.base import OrderRequest
from app.core.config import Settings
from app.repositories.database import transaction


@dataclass(frozen=True)
class AuditRecord:
    client_order_id: str
    status: str
    broker_order_id: str | None
    error_code: str | None


def _serialize_request(request: OrderRequest) -> str:
    return json.dumps(asdict(request), default=lambda value: str(value) if isinstance(value, Decimal) else value)


def _sanitize_message(message: str | None) -> str | None:
    if not message:
        return None
    return message.replace("\n", " ").strip()[:500]


class AuditRepository:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def create_intent(
        self, request: OrderRequest, strategy_ref: str | None = None
    ) -> None:
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO order_audit
                    (client_order_id, strategy_ref, request_json, status)
                VALUES (%s, %s, %s, 'INTENT')
                """,
                (request.client_order_id, strategy_ref, _serialize_request(request)),
            )

    def _finish(
        self,
        client_order_id: str,
        status: str,
        broker_order_id: str | None,
        error_code: str | None,
        message: str | None,
    ) -> None:
        response_json: str | None = None
        clean_message = _sanitize_message(message)
        if clean_message:
            response_json = json.dumps({"message": clean_message})
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE order_audit
                SET status = %s,
                    broker_order_id = %s,
                    error_code = %s,
                    response_json = %s,
                    updated_at = CURRENT_TIMESTAMP(3)
                WHERE client_order_id = %s
                """,
                (status, broker_order_id, error_code, response_json, client_order_id),
            )

    def mark_accepted(
        self, client_order_id: str, broker_order_id: str, message: str | None = None
    ) -> None:
        self._finish(client_order_id, "ACCEPTED", broker_order_id, None, message)

    def mark_rejected(
        self, client_order_id: str, code: str, message: str | None = None
    ) -> None:
        self._finish(client_order_id, "REJECTED", None, code, message)

    def mark_unknown(
        self, client_order_id: str, code: str, message: str | None = None
    ) -> None:
        self._finish(client_order_id, "UNKNOWN", None, code, message)

    def get(self, client_order_id: str) -> AuditRecord | None:
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT client_order_id, status, broker_order_id, error_code
                FROM order_audit
                WHERE client_order_id = %s
                """,
                (client_order_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return AuditRecord(
            client_order_id=row["client_order_id"],
            status=row["status"],
            broker_order_id=row["broker_order_id"],
            error_code=row["error_code"],
        )
