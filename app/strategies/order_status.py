"""Periodic read-only order evidence, independent of GTT snapshot availability."""

from datetime import timedelta


class OrderStatusPoller:
    def __init__(self, repository, broker):
        self.repository = repository
        self.broker = broker
        self.checks = {}

    def poll(self, run, now):
        previous = self.checks.get(run.id)
        if previous and now < previous[0]:
            return run, previous[1]
        intents = [
            intent
            for intent in self.repository.list_intents(run.id)
            if intent.broker_id
        ]
        if not intents:
            # The parent snapshot may not have exposed its exchange order yet.
            return run, True
        # Restart checks immediately. Failed reads also respect the polling window.
        self.checks[run.id] = (now + timedelta(seconds=30), False)
        verified = True
        for intent in intents:
            try:
                order = self.broker.reconcile(
                    intent,
                    verify_tag=not (
                        run.execution_backend == "gtt" and intent.purpose != "exit"
                    ),
                )
                if order is None:
                    raise ValueError("Missing order response")
                run = self.repository.apply_order(intent.id, order)
                self.repository.append_event(
                    run.id,
                    "order_status_checked",
                    {
                        "order_id": order.broker_id,
                        "status": order.status,
                        "filled_quantity": order.filled_quantity,
                    },
                )
                verified = verified and order.status != "unknown"
            except Exception:  # noqa: BLE001 -- broker/SQL exceptions can contain secrets
                verified = False
                self.repository.append_event(
                    run.id,
                    "order_status_unavailable",
                    {
                        "order_id": intent.broker_id,
                        "reason": "Order status not verified",
                    },
                )
        try:
            actual = sum(
                p.quantity
                for p in self.broker.positions()
                if p.instrument == run.contract.instrument_key and p.product == "D"
            )
            self.repository.append_event(
                run.id,
                "position_checked",
                {
                    "instrument": run.contract.instrument_key,
                    "expected_quantity": run.remaining_quantity,
                    "actual_quantity": actual,
                    "status": "matched"
                    if actual == run.remaining_quantity
                    else "mismatch",
                },
            )
            # A mismatch may be an as-yet undiscovered GTT exit child. Management
            # must sync that child, then enforce _position_matches before trading.
        except Exception:  # noqa: BLE001 -- never expose broker response text
            verified = False
            self.repository.append_event(
                run.id,
                "position_check_unavailable",
                {"reason": "Position not verified"},
            )
        self.checks[run.id] = (now + timedelta(seconds=30), verified)
        return run, verified
