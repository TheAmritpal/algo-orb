"""Import a sandbox token or run an explicitly requested sandbox order probe."""

import argparse
import os
from decimal import Decimal
from getpass import getpass

import upstox_client as sdk

from app.core.config import get_sandbox_config
from app.repositories.sandbox_tests import SandboxTestRepository
from app.repositories.tokens import get_latest_token, get_token, save_token
from app.repositories.worker_lock import WorkerLock
from app.services.sandbox import SandboxOrder, run_order_test, sandbox_client


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "import-token", help="Read token without echoing it and save to MySQL"
    )
    sub.add_parser(
        "status", help="Check stored token presence; does not verify with broker"
    )
    probe = sub.add_parser(
        "test-order", help="Place one sandbox LIMIT BUY and request cancellation"
    )
    probe.add_argument("--instrument", required=True)
    probe.add_argument("--quantity", type=int, default=1)
    probe.add_argument("--price", type=Decimal, required=True)
    probe.add_argument("--tick-size", type=Decimal, required=True)
    args = parser.parse_args(argv)
    client = None
    lock = None
    try:
        config = get_sandbox_config()
        if args.command == "import-token":
            token = getpass("Sandbox access token (hidden): ").strip()
            save_token(
                config["client_id"], config["user_id"], token, environment="sandbox"
            )
            print("Sandbox token saved in MySQL. Token is not yet broker-verified.")
            return 0
        token = (
            get_token(config["client_id"], config["user_id"], environment="sandbox")
            if os.environ.get("UPSTOX_SANDBOX_USER_ID")
            else get_latest_token(config["client_id"], environment="sandbox")
        )
        if token is not None:
            config["user_id"] = token.user_id
        if token is None:
            print(
                "No sandbox token saved. Run: uv run python -m app.cli.sandbox import-token"
            )
            return 2
        if args.command == "status":
            print(
                "Sandbox token stored. Broker verification requires an explicit sandbox order test."
            )
            return 0
        request = SandboxOrder(
            args.instrument, args.quantity, args.price, args.tick_size
        )
        limits = {
            "max_quantity": int(os.environ.get("UPSTOX_SANDBOX_MAX_QUANTITY", "1")),
            "max_notional": Decimal(
                os.environ.get("UPSTOX_SANDBOX_MAX_NOTIONAL", "1000")
            ),
        }
        request.validate(**limits)
        lock = WorkerLock(config["client_id"], config["user_id"], "sandbox")
        if not lock.acquire():
            print("A sandbox test is already running for this app/account.")
            return 2
        lock.assert_owned()
        client = sandbox_client(token.access_token)
        result = run_order_test(
            request, sdk.OrderApi(client), SandboxTestRepository(), **config, **limits
        )
        print("Sandbox result:", result)
        print(
            "Acknowledgements do not establish fills or final cancellation. See sandbox_order_tests and sandbox_test_events."
        )
        return 0 if result == "cancel_acknowledged" else 2
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- sanitize broker/DB errors at the CLI boundary
        # SDK and DB exception text can expose credentials and request headers.
        print(
            "Sandbox operation failed. Check configuration, migrations and saved test states before retrying."
        )
        return 2
    finally:
        if client is not None:
            client.rest_client.pool_manager.clear()
            client.pool.close()
            client.pool.join()
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
