"""Read-only sanitized dashboard projection; never starts a worker."""
from dataclasses import dataclass
from datetime import datetime,timezone
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

def get_strategy_status(*,now=None):
    now=now or datetime.now(timezone.utc)
    try:
        settings=load_trading_settings()
        if settings.user_id=="paper":return StrategyStatus(mode=settings.mode)
        repo=StrategyRepository()
        unresolved=repo.list_unresolved(settings.client_id,settings.user_id,settings.mode)
        run=unresolved[0] if unresolved else repo.latest(settings.client_id,settings.user_id,settings.mode)
        if run is None:return StrategyStatus("not_started",settings.mode,message="No strategy run recorded. Worker is not confirmed running.")
        alive=run.heartbeat is not None and 0<=(now-run.heartbeat).total_seconds()<=30
        message="Worker heartbeat is current."
        if not alive:message="Worker heartbeat is stale or absent; open exposure requires attention."
        if run.state=="reconciliation_required":message="Broker outcome or remaining exposure needs reconciliation."
        if settings.mode=="live" and not settings.protective_stop_verified:message="Protective-stop support must be verified before live entries. Existing exposure still requires management."
        return StrategyStatus(run.state,settings.mode,alive,run,message)
    except Exception:
        return StrategyStatus("unavailable",message="Strategy status unavailable. Check worker configuration and migrated database.")
