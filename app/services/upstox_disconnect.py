"""Revoke a selected live connection without interrupting strategy management."""

from contextlib import ExitStack

import pymysql
import upstox_client
from fastapi import HTTPException
from upstox_client.rest import ApiException
from urllib3.exceptions import HTTPError

from app.core.config import get_upstox_config
from app.repositories.strategy import StrategyRepository
from app.repositories.tokens import delete_token, get_token
from app.repositories.worker_lock import WorkerLock


def disconnect(user_id: str) -> None:
    client = None
    try:
        settings = get_upstox_config()
        with ExitStack() as stack:
            # Market-data paper workers use the live token too. Keep both modes
            # excluded until revocation and the conditional delete finish.
            for mode in ("paper", "live", "credentials"):
                lock = WorkerLock(settings["client_id"], user_id, mode)
                stack.callback(lock.close)
                if not lock.acquire():
                    raise HTTPException(
                        409,
                        "Stop the strategy worker and wait for credential renewal before disconnecting Upstox.",
                    )
            if StrategyRepository().list_unresolved(
                settings["client_id"], user_id, "live"
            ):
                raise HTTPException(
                    409,
                    "Resolve live strategy orders and exposure before disconnecting Upstox.",
                )
            token = get_token(settings["client_id"], user_id, environment="live")
            if token is None:
                return
            configuration = upstox_client.Configuration()
            configuration.access_token = token.access_token
            client = upstox_client.ApiClient(configuration)
            try:
                response = upstox_client.LoginApi(client).logout(
                    "2.0", _request_timeout=(3, 5)
                )
                if (
                    getattr(response, "status", None) != "success"
                    or getattr(response, "data", None) is not True
                ):
                    raise HTTPException(
                        502,
                        "Upstox did not confirm disconnection. Saved credentials were retained.",
                    )
            except ApiException as exc:
                # An invalid/expired token already cannot authorize requests.
                if exc.status != 401:
                    raise HTTPException(
                        502,
                        "Unable to confirm Upstox disconnection. Saved credentials were retained; try again.",
                    ) from None
            delete_token(
                settings["client_id"], user_id, token.access_token, environment="live"
            )
    except (HTTPError, OSError):
        raise HTTPException(
            502, "Unable to confirm Upstox disconnection. Try again."
        ) from None
    except (pymysql.MySQLError, ValueError, RuntimeError):
        raise HTTPException(
            503,
            "Unable to finish disconnection. Check database availability and reload the dashboard.",
        ) from None
    finally:
        if client is not None:
            for cleanup in (
                client.rest_client.pool_manager.clear,
                client.pool.close,
                client.pool.join,
            ):
                try:
                    cleanup()
                except (OSError, ValueError, RuntimeError):
                    pass
