from copy import deepcopy

from app.strategies.gtt_models import GttRecord
from tests.test_strategy_rules import at


class MemoryGttRepository:
    def __init__(self, repository):
        self.repository = repository
        self.records = {}
        self.mutations = {}
        self.children = {}
        self.history = {}
        self.clock = at

    def get(self, run_id):
        record = deepcopy(self.records[run_id])
        record.pending = next(
            (
                dict(m, id=id)
                for id, m in self.mutations.items()
                if m["run_id"] == run_id
                and m["status"] == "pending"
                and m.get("attempt", 1) == record.attempt
            ),
            None,
        )
        return record

    def reserve(self, run, request, stop, target):
        if self.repository.get_run(run.id).entry_reserved:
            return None
        self.repository.transition(
            run.id,
            run.version,
            "entry_pending",
            {
                "entry_reserved": True,
                "requested_quantity": request.quantity,
                "execution_backend": "gtt",
                "stop": stop,
                "target": target,
            },
        )
        record = GttRecord(run.id, request, stop, target, at())
        self.records[run.id] = record
        id = len(self.mutations) + 1
        self.mutations[id] = {
            "run_id": run.id,
            "kind": "create",
            "fields": {},
            "status": "pending",
        }
        return self.get(run.id)

    def acknowledge(self, run_id, broker_id, *, activate=False):
        self.records[run_id].broker_id = broker_id
        for m in self.mutations.values():
            if (
                m["run_id"] == run_id
                and m["kind"] == "create"
                and m.get("attempt", 1) == self.records[run_id].attempt
            ):
                m["status"] = "confirmed"
        if activate:
            current = self.repository.get_run(run_id)
            self.repository.transition(
                run_id,
                current.version,
                "gtt_active",
                {
                    "message": (
                        "GTT placed; Upstox manages exits until 15:14 cleanup"
                    )
                },
            )

    def authorize_retry(self, run, record):
        assert not run.filled_quantity and not run.remaining_quantity
        assert record.attempt < 3 and not record.pending
        self.records[run.id].retry_requested = True

    def block_retries(self, run_id):
        self.records[run_id].retry_blocked = True

    def reserve_retry(self, run, previous, request, stop, target):
        current = self.repository.get_run(run.id)
        saved = self.get(run.id)
        if current.version != run.version or saved.attempt != previous.attempt:
            return None
        assert (
            saved.retry_requested
            and saved.attempt < 3
            and not saved.pending
            and not saved.retry_blocked
        )
        assert not current.filled_quantity and not current.remaining_quantity
        assert saved.snapshot.terminal
        self.history[(run.id, saved.attempt)] = deepcopy(saved)
        self.repository.transition(
            run.id, run.version, "entry_pending", {"stop": stop, "target": target}
        )
        self.records[run.id] = GttRecord(
            run.id, request, stop, target, self.clock(), attempt=saved.attempt + 1
        )
        id = len(self.mutations) + 1
        self.mutations[id] = {
            "run_id": run.id,
            "attempt": saved.attempt + 1,
            "kind": "create",
            "fields": {},
            "status": "pending",
        }
        return self.get(run.id)

    def mutation(self, run_id, kind, fields):
        pending = self.get(run_id).pending
        if pending:
            assert kind == "cancel" and pending["kind"] == "modify"
            self.mutation_result(pending["id"], "superseded")
        id = len(self.mutations) + 1
        self.mutations[id] = {
            "run_id": run_id,
            "attempt": self.records[run_id].attempt,
            "kind": kind,
            "fields": fields,
            "status": "pending",
        }
        return id

    def mutation_result(self, id, status):
        self.mutations[id]["status"] = status

    def observe(self, run_id, snapshot):
        self.records[run_id].snapshot = snapshot

    def levels(self, run_id, stop, target):
        self.records[run_id].stop = stop
        self.records[run_id].target = target

    def attach_child(self, run_id, leg, request):
        key = (run_id, self.records[run_id].attempt, leg.strategy)
        if key not in self.children:
            i = self.repository._intent(
                run_id,
                {"ENTRY": "entry", "TARGET": "target", "STOPLOSS": "stop"}[
                    leg.strategy
                ],
                request,
            )
            self.repository.intents[i.id].broker_id = leg.order_id
            self.children[key] = i.id
        i = self.repository.intents[self.children[key]]
        assert i.broker_id == leg.order_id
        return deepcopy(i)
