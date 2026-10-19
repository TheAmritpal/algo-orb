"""Read-only sanitized dashboard projection; never starts a worker."""

from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.trading import load_trading_settings
from app.repositories.strategy import StrategyRepository
from app.strategies.models import StrategyRun


@dataclass(frozen=True)
class StrategyStatus:
    state: str = "not_configured"
    mode: str = "paper"
    worker_alive: bool = False
    run: StrategyRun | None = None
    message: str = "Select a connected broker account to configure the strategy worker."


def get_strategy_status(*, now=None):
    now = now or datetime.now(UTC)
    settings = None
    try:
        settings = load_trading_settings()
        if settings.user_id == "paper":
            return StrategyStatus(mode=settings.mode)
        repo = StrategyRepository()
        # Saved settings apply at next startup. Keep current workers and exposure
        # visible even if the operator has selected another mode for that startup.
        candidates = []
        for mode in (settings.mode, "live" if settings.mode == "paper" else "paper"):
            unresolved = repo.list_unresolved(
                settings.client_id, settings.user_id, mode
            )
            latest = repo.latest(settings.client_id, settings.user_id, mode)
            for candidate in unresolved + ([latest] if latest else []):
                alive = (
                    candidate.heartbeat is not None
                    and 0 <= (now - candidate.heartbeat).total_seconds() <= 30
                )
                if mode == settings.mode or candidate in unresolved or alive:
                    candidates.append(candidate)
        run = max(
            candidates,
            key=lambda candidate: (
                candidate.key.mode == "live" and candidate.remaining_quantity > 0,
                candidate.heartbeat is not None
                and 0 <= (now - candidate.heartbeat).total_seconds() <= 30,
                candidate.key.mode == settings.mode,
            ),
            default=None,
        )
        if run is None:
            return StrategyStatus(
                "not_started",
                settings.mode,
                message="No strategy run recorded. Worker is not confirmed running.",
            )
        alive = (
            run.heartbeat is not None
            and 0 <= (now - run.heartbeat).total_seconds() <= 30
        )
        message = "Worker heartbeat is current."
        if not alive:
            message = (
                "Worker heartbeat is stale or absent; open exposure requires attention."
            )
        if run.state == "reconciliation_required":
            message = "Broker outcome or remaining exposure needs reconciliation."
        if run.key.mode == "live" and not settings.protective_stop_verified:
            message = "Protective-stop support must be verified before live entries. Existing exposure still requires management."
        if run.execution_backend == "gtt":
            message = "GTT execution: Upstox monitors exit triggers. " + message
        return StrategyStatus(run.state, run.key.mode, alive, run, message)
    except Exception:  # noqa: BLE001 -- fail closed without leaking broker/SQL secrets
        return StrategyStatus(
            "unavailable",
            mode=settings.mode if settings is not None else "unknown",
            message="Strategy status unavailable. Check worker configuration and migrated database.",
        )
