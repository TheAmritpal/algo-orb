from copy import deepcopy

from app.strategies.gtt_models import GttRecord
from tests.test_strategy_rules import at


class MemoryGttRepository:
    def __init__(self, repository):
        self.repository = repository
        self.records = {}
        self.mutations = {}
        self.children = {}

    def get(self, run_id):
        record = deepcopy(self.records[run_id])
        record.pending = next(
            (
                dict(m, id=id)
                for id, m in self.mutations.items()
                if m["run_id"] == run_id and m["status"] == "pending"
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

    def acknowledge(self, run_id, broker_id):
        self.records[run_id].broker_id = broker_id
        for m in self.mutations.values():
            if m["run_id"] == run_id and m["kind"] == "create":
                m["status"] = "confirmed"

    def mutation(self, run_id, kind, fields):
        pending = self.get(run_id).pending
        if pending:
            assert kind == "cancel" and pending["kind"] == "modify"
            self.mutation_result(pending["id"], "superseded")
        id = len(self.mutations) + 1
        self.mutations[id] = {
            "run_id": run_id,
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
        key = (run_id, leg.strategy)
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
