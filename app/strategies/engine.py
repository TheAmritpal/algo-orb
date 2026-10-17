"""Durable ORB orchestration with one entry and serialized confirmed exits."""

from datetime import time
from decimal import Decimal
from uuid import uuid4

from app.services.trading_broker import bounded_sell, validate_entry
from app.services.trading_market import TradingUnavailable
from app.strategies.models import TERMINAL, BrokerOrder, OrderRequest, RunKey
from app.strategies.rules import (
    IST,
    UNDERLYING,
    breakout,
    build_range,
    exit_reason,
    premium_levels,
    select_contract,
)


class StrategyEngine:
    def __init__(self, settings, repository, market, broker, ownership, *, clock=None):
        self.settings = settings
        self.repository = repository
        self.market = market
        self.broker = broker
        self.ownership = ownership
        self.entries_enabled = True
        self.clock = clock
        self._tick_time = None

    def stop_entries(self):
        self.entries_enabled = False

    def _state(self, run, state, **changes):
        return self.repository.transition(run.id, run.version, state, changes)

    def _fail(self, run, reason):
        # Reasons are fixed application messages, never broker exception text.
        current = self.repository.get_run(run.id)
        return self._state(current, "reconciliation_required", message=reason)

    def _request(self, run, side, quantity, price, kind="LIMIT", trigger=Decimal(0)):
        return OrderRequest(
            "orb" + uuid4().hex[:17],
            run.contract.instrument_key,
            "D",
            side,
            kind,
            quantity,
            price,
            trigger,
        )

    def _submit(self, run, purpose, request):
        intent = (
            self.repository.reserve_entry(run.id, run.version, request)
            if purpose == "entry"
            else self.repository.create_intent(run.id, run.version, purpose, request)
        )
        if intent is None:
            return self.repository.get_run(run.id)
        self.ownership.assert_owned()
        current = self.clock() if self.clock else self._tick_time
        if purpose == "entry" and (
            current.astimezone(IST).time() > time(15)
            or (current - self._tick_time).total_seconds()
            > self.settings.quote_max_age_seconds
        ):
            # Reserved, but deliberately never submitted. Keep the daily allowance consumed.
            return self.repository.apply_order(
                intent.id,
                BrokerOrder(
                    "local-" + request.tag, request.tag, "rejected", request.quantity, 0
                ),
            )
        result = self.broker.submit(request)
        return self.repository.apply_order(intent.id, result)

    def _cancel(self, run, intent):
        if not intent.broker_id:
            raise RuntimeError("Unknown order identity")
        self.ownership.assert_owned()
        self.broker.cancel(intent.broker_id)
        # A cancellation response alone never allows another sell.
        result = self.broker.reconcile(intent)
        if result is None or result.status not in TERMINAL:
            raise RuntimeError("Cancellation not confirmed")
        return self.repository.apply_order(intent.id, result)

    def _intents(self, run):
        return self.repository.list_intents(run.id)

    def _hydrate(self, run):
        if self.settings.mode == "paper":
            self.broker.restore(self._intents(run))

    def _sync(self, run):
        self._hydrate(run)
        for intent in self._intents(run):
            if intent.status in TERMINAL:
                continue
            result = self.broker.reconcile(intent)
            if result is None or result.status == "unknown":
                raise RuntimeError("Broker outcome unknown")
            run = self.repository.apply_order(intent.id, result)
        return self.repository.get_run(run.id)

    def _position_matches(self, run):
        actual = sum(
            p.quantity
            for p in self.broker.positions()
            if p.instrument == run.contract.instrument_key and p.product == "D"
        )
        if actual != run.remaining_quantity:
            raise RuntimeError("Position attribution mismatch")

    def _quote(self, run, now):
        quote = self.market.quote(run.contract.instrument_key)
        received_at = self.clock() if self.clock else now
        if quote is None or not quote.fresh(
            received_at, self.settings.quote_max_age_seconds
        ):
            raise ValueError("Fresh exit pricing unavailable")
        return quote

    def _protect(self, run, now):
        stops = [
            i
            for i in self._intents(run)
            if i.purpose == "stop" and i.status not in TERMINAL
        ]
        if stops:
            i = stops[0]
            if (
                i.request.quantity - sum(f.quantity for f in i.fills)
                == run.remaining_quantity
                and i.request.trigger_price == run.stop
            ):
                return run
            run = self._cancel(run, i)
            if not run.remaining_quantity:
                return run
        slip = self.settings.limits.max_slippage if self.settings.limits else Decimal(1)
        price = bounded_sell(
            type("StopQuote", (), {"bid": run.stop})(), run.contract, slip
        )
        return self._submit(
            run,
            "stop",
            self._request(run, "SELL", run.remaining_quantity, price, "SL", run.stop),
        )

    def _exit(self, run, now, reason):
        # Preserve any working protective stop if there is no actionable exit price.
        quote = self._quote(run, now)
        for intent in self._intents(run):
            if intent.status in TERMINAL or intent.purpose == "entry":
                continue
            run = self._cancel(run, intent)
        self._position_matches(run)
        if not run.remaining_quantity:
            return self._state(run, "closed", message="Position closed")
        slip = self.settings.limits.max_slippage if self.settings.limits else Decimal(1)
        price = bounded_sell(quote, run.contract, slip)
        self.repository.append_event(run.id, "exit_requested", {"reason": reason})
        return self._submit(
            run, "exit", self._request(run, "SELL", run.remaining_quantity, price)
        )

    def _manage(self, run, now):
        try:
            run = self._sync(run)
            now = self.clock() if self.clock else now
            entry = next((i for i in self._intents(run) if i.purpose == "entry"), None)
            if entry is None:
                raise RuntimeError("Missing entry evidence")
            deadline = run.key.exchange_date < now.astimezone(
                IST
            ).date() or now.astimezone(IST).time() >= time(15, 25)
            # Cancel the unfilled entry immediately once any exit condition occurs.
            # Keeping it working after a partial stop-out could reopen exposure.
            forced_exit = None
            if entry.status not in TERMINAL:
                intents = self._intents(run)
                if deadline:
                    forced_exit = "time"
                elif any(i.purpose != "entry" and i.fills for i in intents):
                    forced_exit = "partial position exit"
                elif any(
                    i.purpose == "stop" and i.status == "rejected" for i in intents
                ):
                    forced_exit = "protective stop rejected"
                if run.remaining_quantity and forced_exit is None:
                    try:
                        stop, target = premium_levels(
                            run.entry_average, run.contract.tick_size
                        )
                    except ValueError:
                        forced_exit = "unrepresentable partial premium protection"
                    else:
                        run = self._state(
                            run, "entry_pending", stop=stop, target=target
                        )
                        run = self._protect(run, now)
                        intents = self._intents(run)
                        if any(i.purpose != "entry" and i.fills for i in intents):
                            forced_exit = "partial position exit"
                        elif any(
                            i.purpose == "stop" and i.status == "rejected"
                            for i in intents
                        ):
                            forced_exit = "protective stop rejected"
                        else:
                            try:
                                quote = self._quote(run, now)
                                forced_exit = exit_reason(
                                    now, quote, run.stop, run.target
                                )
                            except (ValueError, TradingUnavailable):
                                # Preserve protection, but do not postpone entry cancellation.
                                pass
                if forced_exit or (now - entry.submitted_at).total_seconds() >= 30:
                    run = self._cancel(run, entry)
                    entry = next(i for i in self._intents(run) if i.purpose == "entry")
                else:
                    return
            self._position_matches(run)
            if not run.remaining_quantity:
                for i in self._intents(run):
                    if i.status not in TERMINAL:
                        run = self._cancel(run, i)
                return self._state(
                    run,
                    "closed" if run.filled_quantity else "rejected",
                    message="Position closed"
                    if run.filled_quantity
                    else "Entry ended without fills",
                )
            if forced_exit:
                return self._exit(run, now, forced_exit)
            active_exits = [
                i
                for i in self._intents(run)
                if i.purpose == "exit" and i.status not in TERMINAL
            ]
            if active_exits:
                # Cancel/reconcile an unfilled exit before a bounded replacement.
                if (now - active_exits[0].submitted_at).total_seconds() < 30:
                    return
                run = self._cancel(run, active_exits[0])
                if not run.remaining_quantity:
                    return self._state(run, "closed", message="Position closed")
                return self._exit(run, now, "retry confirmed terminal exit")
            try:
                stop, target = premium_levels(run.entry_average, run.contract.tick_size)
            except ValueError:
                return self._exit(run, now, "unrepresentable premium protection")
            run = self._state(
                run,
                "open",
                stop=stop,
                target=target,
                message="Managing confirmed position",
            )
            stop_rejected = any(
                i.purpose == "stop" and i.status == "rejected"
                for i in self._intents(run)
            )
            if deadline or stop_rejected:
                return self._exit(
                    run, now, "time" if deadline else "protective stop rejected"
                )
            quote = self._quote(run, now)
            reason = exit_reason(now, quote, run.stop, run.target)
            if reason:
                return self._exit(run, now, reason)
            run = self._protect(run, now)
            # A rejected stop acknowledgement needs immediate exposure management.
            if any(
                i.purpose == "stop" and i.status == "rejected"
                for i in self._intents(run)
            ):
                return self._exit(run, now, "protective stop rejected")
        except Exception:  # noqa: BLE001 -- fail closed without leaking broker/SQL secrets
            self._fail(run, "Unable to confirm safe execution; reconciliation required")

    def recover(self, now):
        self._tick_time = now
        for run in self.repository.list_unresolved(
            self.settings.client_id, self.settings.user_id, self.settings.mode
        ):
            self.repository.heartbeat(run.id, now)
            self._manage(run, now)

    def tick(self, now):
        self._tick_time = now
        unresolved = self.repository.list_unresolved(
            self.settings.client_id, self.settings.user_id, self.settings.mode
        )
        if unresolved:
            for run in unresolved:
                self.repository.heartbeat(run.id, now)
                self._manage(run, now)
            return
        key = RunKey(
            self.settings.client_id,
            self.settings.user_id,
            "reliance_orb",
            self.settings.mode,
            now.astimezone(IST).date(),
        )
        run = self.repository.get_or_create(key)
        self.repository.heartbeat(run.id, now)
        if (
            run.entry_reserved
            or run.state in {"closed", "rejected", "skipped"}
            or not self.entries_enabled
        ):
            return
        if now.astimezone(IST).time() > time(15):
            self._state(run, "skipped", message="Entry cutoff passed")
            return
        had_signal = False
        try:
            snapshot = self.market.snapshot(now, None)
            if not snapshot.normal_session:
                return
            opening = build_range(snapshot.candles, now)
            if opening is None:
                return
            run = self._state(run, "waiting_signal", opening=opening)
            signal = breakout(snapshot.candles, opening, now, run.processed_candle)
            if signal is None:
                return
            had_signal = True
            uq = snapshot.underlying_quote
            received_at = self.clock() if self.clock else now
            if (
                uq is None
                or uq.instrument_key != UNDERLYING
                or not uq.fresh(received_at, self.settings.quote_max_age_seconds)
            ):
                raise ValueError("Underlying quote unavailable")
            selected = select_contract(
                snapshot.contracts, uq.last, signal.direction, key.exchange_date
            )
            run = self._state(
                run,
                "waiting_signal",
                contract=selected,
            )
            if any(
                p.instrument == selected.instrument_key and p.quantity
                for p in self.broker.positions()
            ):
                raise ValueError("Contract already held")
            if not self.broker.supports_protective_stop(selected):
                raise ValueError("Protective stop capability not verified")
            quote = self.market.quote(selected.instrument_key)
            funds = self.broker.available_funds()
            received_at = self.clock() if self.clock else now
            price = validate_entry(selected, quote, received_at, self.settings, funds)
            run = self._state(
                run, "waiting_signal", processed_candle=signal.candle_close
            )
            # Recheck wall time after persistence before any broker mutation.
            run = self._submit(
                run, "entry", self._request(run, "BUY", selected.quantity, price)
            )
            if run.filled_quantity:
                self._manage(run, now)
        except Exception as exc:  # noqa: BLE001 -- fail closed without leaking broker/SQL secrets
            current = self.repository.get_run(run.id)
            if current.entry_reserved:
                self._fail(current, "Entry outcome uncertain; reconciliation required")
            elif not had_signal or isinstance(exc, TradingUnavailable):
                self._state(
                    current, current.state, message="Waiting for fresh market data"
                )
                # Let the worker apply bounded read backoff; no mutation was attempted.
                raise TradingUnavailable(
                    "Market data temporarily unavailable"
                ) from None
            else:
                self._state(
                    current,
                    "skipped",
                    message="Entry validation or market data unavailable",
                )
