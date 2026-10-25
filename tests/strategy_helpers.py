from copy import deepcopy
from dataclasses import replace

from app.repositories.strategy import reduce_fills
from app.strategies.models import MarketSnapshot, OrderIntent, Quote, StrategyRun
from tests.test_strategy_rules import D, at, candle, contract


class MemoryRepository:
    def __init__(self):
        self.runs = {}
        self.intents = {}
        self.events = []
        self.decisions = {}

    def record_decision(self, run_id, decision, observed_at):
        key = (run_id, decision.candle_close)
        if key in self.decisions:
            previous = self.decisions[key]
            same = all(
                getattr(previous, name) == getattr(decision, name)
                for name in ("close_price", "opening_high", "opening_low", "direction")
            )
            if same and (
                previous.outcome == decision.outcome
                or (
                    previous.outcome in {"signal", "no_signal"}
                    and decision.outcome in {"expired", "processed", "cutoff"}
                )
            ):
                return False
        self.decisions[key] = deepcopy(decision)
        return True

    def get_or_create(self, key):
        for r in self.runs.values():
            if r.key == key:
                return deepcopy(r)
        r = StrategyRun(len(self.runs) + 1, key)
        self.runs[r.id] = r
        return deepcopy(r)

    def get_run(self, id):
        return deepcopy(self.runs[id])

    def list_unresolved(self, client_id, user_id, mode):
        return [
            deepcopy(r)
            for r in self.runs.values()
            if (r.key.client_id, r.key.user_id, r.key.mode)
            == (client_id, user_id, mode)
            and (
                r.remaining_quantity
                or r.state
                in {"entry_pending", "exit_pending", "reconciliation_required", "open"}
            )
        ]

    def list_intents(self, id):
        return [deepcopy(i) for i in self.intents.values() if i.run_id == id]

    def heartbeat(self, id, now):
        self.runs[id].heartbeat = now

    def transition(self, id, expected_version, state, changes):
        r = self.runs[id]
        if r.version != expected_version:
            raise RuntimeError("stale")
        for k, v in changes.items():
            setattr(r, k, v)
        r.state = state
        r.version += 1
        return deepcopy(r)

    def reserve_entry(self, id, version, request):
        r = self.runs[id]
        if r.entry_reserved or r.version != version:
            return None
        self.transition(
            id,
            version,
            "entry_pending",
            {"entry_reserved": True, "requested_quantity": request.quantity},
        )
        return self._intent(id, "entry", request)

    def _intent(self, id, purpose, request):
        i = OrderIntent(len(self.intents) + 1, id, purpose, request, at())
        self.intents[i.id] = i
        return deepcopy(i)

    def create_intent(self, id, version, purpose, request):
        r = self.runs[id]
        assert not any(
            i.purpose != "entry"
            and i.status not in {"complete", "cancelled", "rejected"}
            for i in self.list_intents(id)
        )
        assert request.quantity <= r.remaining_quantity
        self.transition(
            id, version, "exit_pending" if purpose == "exit" else "open", {}
        )
        return self._intent(id, purpose, request)

    def apply_order(self, id, order):
        i = self.intents[id]
        i.broker_id = order.broker_id
        i.status = order.status
        i.fills = list(order.fills)
        r = self.runs[i.run_id]
        new = reduce_fills(r, self.list_intents(r.id))
        new.version += 1
        self.runs[r.id] = new
        return deepcopy(new)

    def append_event(self, id, kind, fields):
        self.events.append((id, kind, fields))


class FakeMarket:
    def next_trading_session(self, now):
        from app.strategies.rules import IST, next_weekday

        return next_weekday(now.astimezone(IST).date())

    def __init__(self, direction="CE"):
        self.now = at()
        self.contract = contract("1480" if direction == "CE" else "1520", direction)
        self.q = Quote("option", self.now, D("99"), D("100"), D("100"))
        self.underlying = Quote(
            "NSE_EQ|INE002A01018", self.now, D("1500"), D("1501"), D("1500")
        )
        self.candles = [candle(), candle(30, "1511" if direction == "CE" else "1489")]

    def quote(self, instrument):
        return self.q

    def snapshot(self, now, contract=None):
        return MarketSnapshot(
            now,
            True,
            self.candles,
            [self.contract],
            self.underlying,
            self.q if contract else None,
        )

    def move(self, now, bid="99", ask="100"):
        self.now = now
        self.q = replace(self.q, timestamp=now, bid=D(bid), ask=D(ask), last=D(bid))
