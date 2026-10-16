"""Standalone strategy worker; never imported by FastAPI application startup."""

import logging
import signal
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event

import upstox_client as sdk

from app.core.trading import TradingSettings, load_trading_settings
from app.repositories.strategy import StrategyRepository
from app.repositories.tokens import get_token
from app.repositories.worker_lock import WorkerLock
from app.services.paper_broker import PaperBroker
from app.services.trading_broker import Broker
from app.services.trading_market import MarketData, sdk_data
from app.strategies.engine import StrategyEngine

logger = logging.getLogger(__name__)


@dataclass
class Components:
    lock: WorkerLock
    engine: StrategyEngine
    broker: object
    market: MarketData
    client: object


def build_components(settings: TradingSettings) -> Components:
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
        market = MarketData(client)
        repo = StrategyRepository()
        lock = WorkerLock(settings.client_id, settings.user_id, settings.mode)
        broker = PaperBroker(market.quote) if settings.mode == "paper" else live_broker
        engine = StrategyEngine(
            settings,
            repo,
            market,
            broker,
            lock,
            clock=lambda: datetime.now(UTC),
        )
        return Components(lock, engine, broker, market, client)
    except Exception:
        live_broker.close()
        raise


def run_worker(settings: TradingSettings, stop: Event, *, clock=None) -> int:
    clock = clock or (lambda: datetime.now(UTC))
    components = None
    try:
        components = build_components(settings)
        if not components.lock.acquire():
            logger.error("Strategy worker is already running for this account and mode")
            return 2
        components.lock.assert_owned()
        components.engine.recover(clock())
        delay = settings.poll_seconds
        while not stop.is_set():
            try:
                components.lock.assert_owned()
            except Exception:  # noqa: BLE001 -- fail closed without leaking broker/SQL secrets
                logger.error("Strategy worker ownership lost; stopping submissions")
                return 2
            try:
                components.engine.tick(clock())
                delay = settings.poll_seconds
            except Exception:  # noqa: BLE001 -- fail closed without leaking broker/SQL secrets
                logger.error(
                    "Strategy iteration unavailable; persisted exposure requires reconciliation"
                )
                delay = min(30, max(settings.poll_seconds, delay * 2))
            stop.wait(delay)
        return 0
    except Exception:  # noqa: BLE001 -- fail closed without leaking broker/SQL secrets
        logger.error(
            "Strategy worker startup failed; check account, configuration, and migrated database"
        )
        return 2
    finally:
        if components is not None:
            components.engine.stop_entries()
            logger.info(
                "Worker stopping; any remaining NRML exposure needs a running worker or manual management"
            )
            try:
                components.lock.close()
            finally:
                components.broker.close()
                if (
                    settings.mode == "paper"
                    and getattr(components, "client", None) is not None
                ):
                    Broker(components.client).close()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        settings = load_trading_settings()
    except (ValueError, ArithmeticError):
        logger.error("Invalid trading configuration")
        return 2
    return run_worker(settings, stop)


if __name__ == "__main__":
    raise SystemExit(main())
