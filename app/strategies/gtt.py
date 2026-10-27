"""GTT orchestration: broker triggers exits; application reconciles ownership."""

from dataclasses import replace
from datetime import time
from decimal import Decimal
from uuid import uuid4

from app.services.trading_broker import (
    EntryValidationError,
    PositionMismatchError,
    bounded_sell,
    validate_entry,
)
from app.services.trading_market import BrokerOrderNotFound
from app.strategies.gtt_models import LEG_TERMINAL
from app.strategies.models import TERMINAL, OrderRequest
from app.strategies.rules import IST, premium_levels


class GttExecution:
    def __init__(self, engine, broker, repository):
        self.engine = engine
        self.broker = broker
        self.repository = repository

    def enter(self, run, request, now, *, previous=None):
        engine = self.engine
        current = engine.clock() if engine.clock else now
        if (
            current.astimezone(IST).time() > time(15)
            or (current - now).total_seconds() > engine.settings.quote_max_age_seconds
            or (previous is not None and not self._retry_window(run, previous, current))
        ):
            return engine._state(run, "skipped", message="GTT entry pricing expired")
        stop, target = premium_levels(request.price, run.contract.tick_size)
        record = (
            self.repository.reserve_retry(run, previous, request, stop, target)
            if previous is not None
            else self.repository.reserve(run, request, stop, target)
        )
        if record is None:
            return engine.repository.get_run(run.id)
        engine.ownership.assert_owned()
        current = engine.clock() if engine.clock else now
        if (
            current.astimezone(IST).time() > time(15)
            or not 0
            <= (current - now).total_seconds()
            <= engine.settings.quote_max_age_seconds
            or (previous is not None and not self._retry_window(run, previous, current))
        ):
            self.repository.mutation_result(record.pending["id"], "not_submitted")
            return engine._state(
                engine.repository.get_run(run.id),
                "rejected",
                message="GTT entry expired before submission",
            )
        # Creation uncertainty remains reserved forever until explicitly reconciled.
        broker_id = self.broker.place(record)
        self.repository.acknowledge(run.id, broker_id)
        current = engine.repository.get_run(run.id)
        if run.key.mode == "live":
            return engine._state(
                current,
                "gtt_active",
                message="GTT placed; Upstox manages exits until 15:14 cleanup",
            )
        return current

    def cleanup(self, run, now):
        """Cancel the day's GTT and flatten once, at 15:14 IST."""
        engine = self.engine
        if run.state == "closed":
            return run
        if now.astimezone(IST).time() < time(15, 14):
            return run
        if run.state in {"closing", "exit_pending"}:
            return self._resume_cleanup(run)
        if run.state != "gtt_active":
            return run
        record = self.repository.get(run.id)
        if not record.broker_id:
            return engine._fail(run, "GTT identity unknown; reconciliation required")
        try:
            snapshot = self.broker.status(record)
        except BrokerOrderNotFound:
            snapshot = None
        except Exception:  # noqa: BLE001 -- never expose broker/SQL secrets
            return engine._fail(
                run, "GTT cleanup read failed; reconciliation required"
            )
        if snapshot is not None and not snapshot.terminal:
            try:
                self.broker.cancel(record)
            except BrokerOrderNotFound:
                pass
            except Exception:  # noqa: BLE001 -- never expose broker/SQL secrets
                return engine._fail(
                    run, "GTT cancellation uncertain; reconciliation required"
                )
            for leg in snapshot.legs:
                if leg.order_id and leg.status in {"OPEN", "TRIGGERED", "PENDING"}:
                    try:
                        engine.broker.cancel(leg.order_id)
                    except BrokerOrderNotFound:
                        pass
                    except Exception:  # noqa: BLE001 -- never expose secrets
                        return engine._fail(
                            run,
                            "GTT child cancellation uncertain; reconciliation required",
                        )
            try:
                after = self.broker.status(record)
            except BrokerOrderNotFound:
                after = None
            except Exception:  # noqa: BLE001 -- never expose broker/SQL secrets
                return engine._fail(
                    run, "GTT cancellation unverified; reconciliation required"
                )
            if after is not None and any(
                leg.status not in LEG_TERMINAL for leg in after.legs
            ):
                return engine._fail(
                    run,
                    "GTT parent or children still active; reconciliation required",
                )
        try:
            actual = sum(
                p.quantity
                for p in engine.broker.positions()
                if p.instrument == run.contract.instrument_key and p.product == "I"
            )
        except Exception:  # noqa: BLE001 -- never expose broker/SQL secrets
            return engine._fail(
                run, "Position not verified; reconciliation required"
            )
        engine.repository.append_event(
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
        if actual == 0:
            return engine._state(run, "closed", message="Day-end cleanup: flat")
        if actual < 0:
            return engine._fail(
                run, "Unexpected short position; reconciliation required"
            )
        try:
            quote = engine._quote(run, now)
            slip = (
                engine.settings.limits.max_slippage
                if engine.settings.limits
                else Decimal(1)
            )
            price = bounded_sell(quote, run.contract, slip)
        except Exception:  # noqa: BLE001 -- never expose broker/SQL secrets
            return engine._fail(
                run, "Exit pricing unavailable; reconciliation required"
            )
        run = engine._state(
            run,
            "closing",
            filled_quantity=actual,
            remaining_quantity=actual,
            message="Day-end exit in progress",
        )
        request = replace(
            engine._request(run, "SELL", actual, price), product="I"
        )
        intent = engine.repository.create_intent(
            run.id, run.version, "exit", request
        )
        run = engine.repository.get_run(run.id)
        engine.ownership.assert_owned()
        try:
            order = engine.broker.submit(request)
        except Exception:  # noqa: BLE001 -- never expose broker/SQL secrets
            return engine._fail(
                run, "Exit submission uncertain; reconciliation required"
            )
        engine.repository.record_submission(intent.id, order.broker_id)
        engine.repository.append_event(
            run.id,
            "cleanup_exit_submitted",
            {
                "order_id": order.broker_id,
                "quantity": actual,
                "reason": "15:14 day-end cleanup",
            },
        )
        return engine._state(
            engine.repository.get_run(run.id),
            "closed",
            remaining_quantity=0,
            message="Exit submitted at 15:14; MIS auto-square-off backstop",
        )

    def _resume_cleanup(self, run):
        exits = [i for i in self.engine._intents(run) if i.purpose == "exit"]
        if exits and exits[-1].broker_id:
            return self.engine._state(
                run,
                "closed",
                remaining_quantity=0,
                message="Day-end exit previously submitted; broker backstop",
            )
        return self.engine._fail(
            run, "Exit submission outcome unknown; reconciliation required"
        )

    def _retry_window(self, run, record, now):
        return (
            self.engine.entries_enabled
            and run.state != "reconciliation_required"
            and record.attempt < 3
            and not record.retry_blocked
            and now.astimezone(IST).date() == run.key.exchange_date
            and now.astimezone(IST).time() < time(15)
        )

    def _retry_entry(self, run, record, now):
        engine = self.engine
        try:
            if run.contract.expiry <= engine.market.next_trading_session(now):
                raise EntryValidationError("expiry_restricted")
            if not engine.market.snapshot(now, run.contract).normal_session:
                raise EntryValidationError("market_closed")
            if not engine.broker.supports_protective_stop(run.contract):
                raise EntryValidationError("protection_unverified")
            if any(
                p.instrument == run.contract.instrument_key and p.quantity
                for p in engine.broker.positions()
            ):
                raise EntryValidationError("contract_already_held")
            quote = engine.market.quote(run.contract.instrument_key)
            funds = engine.broker.available_funds()
            received_at = engine.clock() if engine.clock else now
            price = validate_entry(
                run.contract, quote, received_at, engine.settings, funds
            )
        except EntryValidationError as exc:
            return engine._state(
                run, "rejected", message="GTT retry blocked: " + exc.reason
            )
        if not self._retry_window(run, record, received_at):
            return engine._state(
                run, "rejected", message="GTT entry retry window ended"
            )
        engine._position_matches(run)
        engine.repository.append_event(
            run.id,
            "entry_retry",
            {
                "reason": "unfilled_timeout",
                "price": price,
                "quantity": run.requested_quantity,
            },
        )
        return self.enter(
            run,
            engine._request(run, "BUY", run.requested_quantity, price),
            quote.timestamp,
            previous=record,
        )

    def _sync(self, run, record):
        engine = self.engine
        snapshot = self.broker.snapshot(record)
        self.repository.observe(run.id, snapshot)
        # Apply entry fills before any sell fills, independent of broker rule order.
        for leg in sorted(snapshot.legs, key=lambda leg: leg.strategy != "ENTRY"):
            if not leg.order_id:
                continue
            request = OrderRequest(
                "gtt" + uuid4().hex[:17],
                record.request.instrument,
                record.request.product,
                leg.side,
                "LIMIT",
                record.request.quantity,
                leg.price,
            )
            intent = self.repository.attach_child(run.id, leg, request)
            order = self.broker.child_order(record, leg, intent.request)
            if order.status == "unknown":
                raise RuntimeError("Unknown GTT exchange order")
            run = engine.repository.apply_order(intent.id, order)
        for intent in engine._intents(run):
            if intent.purpose != "exit" or intent.status in TERMINAL:
                continue
            order = engine.broker.reconcile(intent)
            if order is None or order.status == "unknown":
                raise RuntimeError("Unknown controlled exit outcome")
            run = engine.repository.apply_order(intent.id, order)
        return engine.repository.get_run(run.id), snapshot

    def _resume_mutation(self, run, record, snapshot):
        pending = record.pending
        if pending is None:
            return True
        fields = pending["fields"]
        if pending["kind"] == "modify":
            prices = {leg.strategy: leg.price for leg in snapshot.legs}
            if (prices["STOPLOSS"], prices["TARGET"]) != (
                Decimal(str(fields["stop"])),
                Decimal(str(fields["target"])),
            ):
                return False
            self.repository.levels(run.id, prices["STOPLOSS"], prices["TARGET"])
        elif pending["kind"] == "cancel":
            if not self._triggers_disabled(snapshot):
                raise RuntimeError("GTT cancellation outcome uncertain")
        elif pending["kind"] == "cancel_child":
            intent = next(
                i
                for i in self.engine._intents(run)
                if i.broker_id == fields["order_id"]
            )
            if intent.status not in TERMINAL:
                raise RuntimeError("GTT child cancellation outcome uncertain")
        else:
            raise RuntimeError("GTT creation outcome uncertain")
        self.repository.mutation_result(pending["id"], "confirmed")
        return True

    @staticmethod
    def _triggers_disabled(snapshot):
        # An already generated working order must be cancelled separately.
        return all(
            leg.status in LEG_TERMINAL
            or (leg.status in {"OPEN", "TRIGGERED"} and leg.order_id)
            for leg in snapshot.legs
        )

    def _cancel_child(self, run, record, intent):
        if not intent.broker_id:
            raise RuntimeError("Unknown exchange child identity")
        mutation = self.repository.mutation(
            run.id, "cancel_child", {"order_id": intent.broker_id}
        )
        self.engine.ownership.assert_owned()
        self.engine.broker.cancel(intent.broker_id)
        run, snapshot = self._sync(run, record)
        current = next(i for i in self.engine._intents(run) if i.id == intent.id)
        if current.status not in TERMINAL:
            raise RuntimeError("Exchange cancellation not confirmed")
        self.repository.mutation_result(mutation, "confirmed")
        return run, snapshot

    def _drain(self, run, record, snapshot, *, force_cancel=False):
        """Disable parent triggers AND working generated orders before local sells."""
        if force_cancel and snapshot.terminal:
            self.repository.mutation_result(record.pending["id"], "superseded")
        if (force_cancel and not snapshot.terminal) or not self._triggers_disabled(
            snapshot
        ):
            mutation = self.repository.mutation(run.id, "cancel", {})
            self.engine.ownership.assert_owned()
            self.broker.cancel(record)
            run, snapshot = self._sync(run, record)
            if not self._triggers_disabled(snapshot):
                raise RuntimeError("Parent cancellation not confirmed")
            self.repository.mutation_result(mutation, "confirmed")
        for intent in self.engine._intents(run):
            if intent.purpose != "exit" and intent.status not in TERMINAL:
                run, snapshot = self._cancel_child(run, record, intent)
        # Refresh again to discover orders generated while cancellation was in flight.
        run, snapshot = self._sync(run, record)
        if not snapshot.terminal or any(
            i.purpose != "exit" and i.status not in TERMINAL
            for i in self.engine._intents(run)
        ):
            raise RuntimeError("GTT parent or children still active")
        return run, snapshot

    def _finish_or_exit(
        self, run, record, snapshot, now, reason, *, force_cancel=False
    ):
        engine = self.engine
        run, snapshot = self._drain(run, record, snapshot, force_cancel=force_cancel)
        engine._position_matches(run)
        exits = [
            i
            for i in engine._intents(run)
            if i.purpose == "exit" and i.status not in TERMINAL
        ]
        if exits:
            if (now - exits[0].submitted_at).total_seconds() < 30:
                return run
            run, snapshot = self._cancel_child(run, record, exits[0])
            engine._position_matches(run)
        if not run.remaining_quantity:
            current = engine.clock() if engine.clock else now
            if (
                not run.filled_quantity
                and record.retry_requested
                and self._retry_window(run, record, current)
                and all(leg.status == "CANCELLED" for leg in snapshot.legs)
                and all(
                    i.status == "cancelled" and not i.fills
                    for i in engine._intents(run)
                )
            ):
                return self._retry_entry(run, record, current)
            return engine._state(
                run,
                "closed" if run.filled_quantity else "rejected",
                message="GTT and exchange orders resolved",
            )
        return engine._exit(run, now, reason)

    def manage(self, run, now):
        engine = self.engine
        try:
            record = self.repository.get(run.id)
            if run.state == "reconciliation_required" and not record.retry_blocked:
                self.repository.block_retries(run.id)
                record = self.repository.get(run.id)
            if not record.broker_id:
                raise RuntimeError("GTT creation identity unknown")
            if run.key.mode == "paper":
                self.broker.restore(record, engine._intents(run))
            run, snapshot = self._sync(run, record)
            if not self._resume_mutation(run, record, snapshot):
                return self._finish_or_exit(
                    run,
                    record,
                    snapshot,
                    now,
                    "GTT protection update unconfirmed",
                    force_cancel=True,
                )
            record = self.repository.get(run.id)
            now = engine.clock() if engine.clock else now
            deadline = run.key.exchange_date < now.astimezone(
                IST
            ).date() or now.astimezone(IST).time() >= time(15, 25)
            engine._position_matches(run)
            entry = next(leg for leg in snapshot.legs if leg.strategy == "ENTRY")
            exits = [leg for leg in snapshot.legs if leg.strategy != "ENTRY"]
            any_sell = any(
                i.purpose != "entry" and i.fills for i in engine._intents(run)
            )
            partial = 0 < run.filled_quantity < run.requested_quantity
            timeout = (
                entry.status != "COMPLETED"
                and (now - record.created_at).total_seconds() >= 30
            )
            failure = any(
                leg.status in {"FAILED", "EXPIRED", "CANCELLED"} for leg in exits
            ) or entry.status in {"FAILED", "EXPIRED", "CANCELLED"}
            if entry.status == "COMPLETED" and any(
                leg.status in {"INACTIVE", "PENDING"} for leg in exits
            ):
                failure = True
            if (
                deadline
                or partial
                or timeout
                or failure
                or any_sell
                or snapshot.terminal
            ):
                if (
                    timeout
                    and not failure
                    and not partial
                    and not any_sell
                    and not run.filled_quantity
                    and not record.retry_requested
                    and self._retry_window(run, record, now)
                ):
                    self.repository.authorize_retry(run, record)
                    record = self.repository.get(run.id)
                reason = "time" if deadline else "GTT partial fill or terminal leg"
                return self._finish_or_exit(run, record, snapshot, now, reason)
            if not run.filled_quantity:
                return engine._state(
                    run, "entry_pending", message="Waiting for GTT entry fill"
                )
            if entry.status != "COMPLETED":
                raise RuntimeError("Unconfirmed GTT entry completion")
            try:
                stop, target = premium_levels(run.entry_average, run.contract.tick_size)
            except ValueError:
                return self._finish_or_exit(
                    run, record, snapshot, now, "Unrepresentable GTT protection"
                )
            prices = {leg.strategy: leg.price for leg in snapshot.legs}
            if (prices["STOPLOSS"], prices["TARGET"]) != (stop, target):
                mutation = self.repository.mutation(
                    run.id, "modify", {"stop": stop, "target": target}
                )
                engine.ownership.assert_owned()
                self.broker.modify(record, stop, target)
                run, snapshot = self._sync(run, record)
                prices = {leg.strategy: leg.price for leg in snapshot.legs}
                if (prices["STOPLOSS"], prices["TARGET"]) != (stop, target):
                    raise RuntimeError("GTT levels not confirmed")
                self.repository.levels(run.id, stop, target)
                self.repository.mutation_result(mutation, "confirmed")
                # An exit can execute during modification/read-back.
                if run.remaining_quantity != run.requested_quantity or any(
                    leg.status in LEG_TERMINAL
                    for leg in snapshot.legs
                    if leg.strategy != "ENTRY"
                ):
                    return self._finish_or_exit(
                        run, record, snapshot, now, "GTT exit during modification"
                    )
            return engine._state(
                run,
                "open",
                stop=stop,
                target=target,
                message="Upstox GTT monitors target and stop-loss",
            )
        except Exception as exc:  # noqa: BLE001 -- never expose broker/SQL exception text
            try:
                self.repository.block_retries(run.id)
            finally:
                engine._fail(
                    run,
                    "Broker position mismatch; possible manual exit; reconciliation required"
                    if isinstance(exc, PositionMismatchError)
                    else "GTT outcome uncertain; reconciliation required",
                )
