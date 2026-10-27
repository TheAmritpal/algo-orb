"""One-time, dry-run-first repair for the 2026-10-26 manual exit."""

import argparse
import hashlib
import json
import re
from decimal import Decimal

import upstox_client as sdk

from app.core.config import get_upstox_config
from app.repositories.strategy import StrategyRepository
from app.repositories.tokens import get_token
from app.repositories.worker_lock import WorkerLock
from app.services.trading_broker import Broker
from app.services.trading_market import sdk_data
from app.strategies.models import BrokerOrder, Fill, OrderRequest

RUN_ID = 160103
ENTRY_ORDER_ID = "261026000111597"
ENTRY_TRADES = (
    ("370751", 500, Decimal("45.75")),
    ("370752", 500, Decimal("45.80")),
)
EXIT_TRADES = (
    ("753152", 500, Decimal("45.95")),
    ("753153", 500, Decimal("45.95")),
)


def _synthetic(prefix, run_id, trade_ids):
    digest = hashlib.sha256(
        f"{run_id}:{','.join(trade_ids)}".encode()
    ).hexdigest()[:16]
    return prefix + digest


def _failure_details(exc):
    name = type(exc).__name__
    allowed = {
        "ApiException",
        "TimeoutError",
        "ConnectionError",
        "ValueError",
        "TypeError",
        "AttributeError",
        "OperationalError",
        "IntegrityError",
        "TradingUnavailable",
        "BrokerOrderNotFound",
    }
    result = {
        "operation": "manual_exit_repair",
        "error_type": name if name in allowed else "UnexpectedError",
    }
    status = getattr(exc, "http_status", None)
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    code = getattr(exc, "broker_code", None)
    if isinstance(code, str) and re.fullmatch(r"UDAPI[0-9]{1,12}", code):
        result["broker_code"] = code
    return result


def reconcile_manual_exit(manifest, *, apply=False):
    run_id = (manifest or {}).get("run_id", RUN_ID)
    repo = StrategyRepository()
    run = repo.get_run(run_id)
    config = get_upstox_config()
    if run.key.client_id != config["client_id"]:
        raise ValueError("Configured app does not own this run")
    token = get_token(run.key.client_id, run.key.user_id, environment="live")
    if token is None:
        raise ValueError("Selected account has no stored live token")
    configuration = sdk.Configuration()
    configuration.access_token = token.access_token
    client = sdk.ApiClient(configuration)
    client.rest_client.pool_manager.connection_pool_kw["retries"] = False
    lock = None
    try:
        profile = sdk_data(sdk.UserApi(client).get_profile, "2.0")
        if profile.user_id != run.key.user_id:
            raise ValueError("Authenticated broker account does not match the run")
        if any(
            p.instrument == run.contract.instrument_key
            and p.product == "D"
            and p.quantity
            for p in Broker(client).positions()
        ):
            raise ValueError("Contract is not flat at the broker")
        intents = repo.list_intents(run.id)
        entry_intent = next(
            (i for i in intents if i.purpose == "entry"), None
        )
        if entry_intent is None or entry_intent.broker_id != ENTRY_ORDER_ID:
            raise ValueError("Entry intent does not match the incident")
        quantity = run.requested_quantity
        entry_order = BrokerOrder(
            ENTRY_ORDER_ID,
            entry_intent.request.tag,
            "complete",
            quantity,
            quantity,
            tuple(
                Fill(trade_id, ENTRY_ORDER_ID, trade_quantity, price)
                for trade_id, trade_quantity, price in ENTRY_TRADES
            ),
        )
        exit_tag = _synthetic(
            "hist", run.id, [entry[0] for entry in EXIT_TRADES]
        )
        fill_order_id = _synthetic(
            "histfill", run.id, [entry[0] for entry in EXIT_TRADES]
        )
        exit_request = OrderRequest(
            exit_tag,
            run.contract.instrument_key,
            "D",
            "SELL",
            "LIMIT",
            quantity,
            EXIT_TRADES[0][2],
        )
        exit_order = BrokerOrder(
            fill_order_id,
            exit_tag,
            "complete",
            quantity,
            quantity,
            tuple(
                Fill(trade_id, fill_order_id, trade_quantity, price)
                for trade_id, trade_quantity, price in EXIT_TRADES
            ),
        )
        if apply:
            lock = WorkerLock(run.key.client_id, run.key.user_id, "live")
            if not lock.acquire():
                raise ValueError(
                    "Stop the strategy worker before applying the repair"
                )
            run = repo.get_run(run.id)
        return repo.repair_manual_exit(
            run, entry_order, exit_request, exit_order, apply=apply
        )
    finally:
        try:
            if lock is not None:
                lock.close()
        finally:
            client.rest_client.pool_manager.clear()
            client.pool.close()
            client.pool.join()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the audited repair; the strategy worker must be stopped",
    )
    args = parser.parse_args(argv)
    try:
        result = reconcile_manual_exit({"run_id": RUN_ID}, apply=args.apply)
    except Exception as exc:  # noqa: BLE001 -- never print credentials or bodies
        print(
            json.dumps(
                {
                    "error": "Manual exit repair refused; no partial repair committed",
                    **_failure_details(exc),
                },
                default=str,
            )
        )
        return 1
    print(json.dumps(result, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
