from __future__ import annotations

from dataclasses import replace

from app.brokers.base import OrderRequest, OrderResult, OrderSnapshot
from app.core.errors import BrokerRejected


class PaperBroker:
    def __init__(self) -> None:
        self._orders: dict[str, OrderSnapshot] = {}

    def place_order(self, request: OrderRequest) -> OrderResult:
        broker_order_id = f"paper-{request.client_order_id}"
        if broker_order_id in self._orders:
            raise BrokerRejected("Duplicate client order id")
        self._orders[broker_order_id] = OrderSnapshot(
            broker_order_id=broker_order_id,
            status="ACCEPTED",
        )
        return OrderResult(broker_order_id=broker_order_id, status="ACCEPTED")

    def get_order(self, broker_order_id: str) -> OrderSnapshot:
        try:
            return self._orders[broker_order_id]
        except KeyError:
            raise BrokerRejected("Unknown paper order") from None

    def cancel_order(self, broker_order_id: str) -> OrderSnapshot:
        snapshot = self.get_order(broker_order_id)
        if snapshot.status == "ACCEPTED":
            snapshot = replace(snapshot, status="CANCELLED")
            self._orders[broker_order_id] = snapshot
        return snapshot
