"""Standalone strategy worker; never imported by FastAPI application startup."""

import logging
import signal
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event

import pymysql
import upstox_client as sdk

from app.core.trading import TradingSettings, load_trading_settings
from app.repositories.strategy import StrategyRepository
from app.repositories.tokens import get_token
from app.repositories.worker_lock import ExecutionOwnership, WorkerLock
from app.services.gtt_broker import GttBroker
from app.services.paper_broker import PaperBroker
from app.services.paper_gtt import PaperGttBroker
from app.services.trading_broker import Broker
from app.services.trading_market import MarketData, sdk_data
from app.strategies.engine import StrategyEngine

logger = logging.getLogger(__name__)


@dataclass
class Components:
    lock: WorkerLock
    engine: StrategyEngine
    broker: Broker | PaperBroker
    market: MarketData
    client: sdk.ApiClient


def build_components(settings: TradingSettings) -> Components:
    if settings.mode not in {"paper", "live"}:
        raise ValueError(
            "Full strategy execution supports paper or live; use the sandbox order probe for sandbox APIs"
        )
    token = get_token(settings.client_id, settings.user_id)
    if token is None:
        raise ValueError(
            "Connect the selected broker account and set ORB_BROKER_USER_ID"
        )
    configuration = sdk.Configuration()
    configuration.access_token = token.access_token
    client = sdk.ApiClient(configuration)
    # Disable urllib3 transport retries, including uncertain mutation responses.
    client.rest_client.pool_manager.connection_pool_kw["retries"] = False
    live_broker = Broker(
        client, protective_stop_verified=settings.protective_stop_verified
    )
    try:
        profile = sdk_data(sdk.UserApi(client).get_profile, "2.0")
        if profile.user_id != settings.user_id:
            raise ValueError("Selected account does not match broker token")
        live_broker.profile = profile
        market = MarketData(client)
        repo = StrategyRepository()
        lock = WorkerLock(settings.client_id, settings.user_id, settings.mode)
        broker = (
            PaperBroker(
                market.quote,
                clock=lambda: datetime.now(UTC),
                quote_max_age_seconds=settings.quote_max_age_seconds,
            )
            if settings.mode == "paper"
            else live_broker
        )
        engine = StrategyEngine(
            settings,
            repo,
            market,
            broker,
            lock,
            clock=lambda: datetime.now(UTC),
            gtt_broker=PaperGttBroker(broker)
            if settings.mode == "paper"
            else GttBroker(client),
        )
        return Components(lock, engine, broker, market, client)
    except Exception:
        live_broker.close()
        raise


def refresh_worker_token(components: Components, settings: TradingSettings) -> None:
    """Adopt verified credentials in place; preserve simulated and pending orders."""
    token = get_token(settings.client_id, settings.user_id)
    if token is None:
        raise ValueError("Selected broker account is disconnected")
    configuration = components.client.configuration
    if token.access_token == configuration.access_token:
        return
    previous = configuration.access_token
    configuration.access_token = token.access_token
    try:
        profile = sdk_data(sdk.UserApi(components.client).get_profile, "2.0")
        if profile.user_id != settings.user_id:
            raise ValueError("Selected account does not match broker token")
    except Exception:
        configuration.access_token = previous
        raise
    logger.info("Strategy worker adopted verified broker credentials")


def close_components(components: Components, settings: TradingSettings) -> None:
    components.engine.stop_entries()
    try:
        components.lock.close()
    finally:
        try:
            components.broker.close()
        finally:
            if settings.mode == "paper":
                Broker(components.client).close()


def run_worker(settings: TradingSettings, stop: Event, *, clock=None) -> int:
    clock = clock or (lambda: datetime.now(UTC))
    components = None
    recovered = False
    delay = settings.poll_seconds
    try:
        while not stop.is_set():
            credentials = WorkerLock(
                settings.client_id, settings.user_id, "credentials"
            )
            try:
                # The scheduler can rotate only between complete iterations.
                if not credentials.acquire():
                    continue
                if components is None:
                    candidate = build_components(settings)
                    try:
                        if not candidate.lock.acquire():
                            logger.error(
                                "Strategy worker is already running for this account and mode"
                            )
                            return 2
                        components = candidate
                    finally:
                        if components is None:
                            close_components(candidate, settings)
                try:
                    components.lock.assert_owned()
                except Exception:  # noqa: BLE001 -- never log database exceptions
                    logger.error("Strategy worker ownership lost; stopping submissions")
                    return 2
                components.engine.ownership = ExecutionOwnership(
                    components.lock, credentials
                )
                components.engine.ownership.assert_owned()
                refresh_worker_token(components, settings)
                if not recovered:
                    components.engine.recover(clock())
                    recovered = True
                components.engine.tick(clock())
                delay = settings.poll_seconds
            except Exception:  # noqa: BLE001 -- broker/SQL errors can contain secrets
                logger.error(
                    "Strategy iteration unavailable; waiting for broker credentials or data. "
                    "Any unresolved orders remain subject to reconciliation."
                )
                delay = min(30, max(settings.poll_seconds, delay * 2))
            finally:
                try:
                    credentials.close()
                except Exception:  # noqa: BLE001 -- discard lease and reconcile under a fresh one
                    logger.error(
                        "Credential lease cleanup unavailable; reacquiring before reconciliation"
                    )
                    recovered = False
                    delay = min(30, max(settings.poll_seconds, delay * 2))
                if not stop.is_set():
                    stop.wait(delay)
        return 0
    finally:
        if components is not None:
            logger.info(
                "Worker stopping; any remaining NRML exposure needs a running worker or manual management"
            )
            try:
                close_components(components, settings)
            except Exception:  # noqa: BLE001 -- never expose broker or database errors
                logger.error(
                    "Worker resource cleanup unavailable; check broker and database availability"
                )


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        settings = load_trading_settings()
    except (ValueError, ArithmeticError, OSError, RuntimeError, pymysql.MySQLError):
        logger.error("Invalid trading configuration")
        return 2
    return run_worker(settings, stop)


if __name__ == "__main__":
    raise SystemExit(main())
