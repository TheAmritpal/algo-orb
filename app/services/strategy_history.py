"""Account-scoped history from MySQL; never calls a broker or starts execution."""

from dataclasses import dataclass, field


@dataclass
class StrategyHistory:
    mode: str = "paper"
    runs: list = field(default_factory=list)
    selected: object | None = None
    decisions: list = field(default_factory=list)
    orders: list = field(default_factory=list)
    events: list = field(default_factory=list)
    gtt: object | None = None
    page: int = 1
    message: str = ""


def get_history(*, mode=None, run_id=None, page=1):
    from app.core.trading import load_trading_settings
    from app.repositories.strategy import StrategyRepository

    history = StrategyHistory(page=page)
    try:
        settings = load_trading_settings()
        history.mode = mode or settings.mode
        if settings.user_id == "paper":
            history.message = "Select a broker account to view strategy history."
            return history
        repository = StrategyRepository()
        history.runs = repository.history_runs(
            settings.client_id, settings.user_id, history.mode, page=page
        )
        if run_id is not None:
            history.selected = next(
                (run for run in history.runs if run.id == run_id), None
            )
            if history.selected is None:
                history.message = (
                    "Session is not available on this account history page."
                )
                return history
        elif history.runs:
            history.selected = history.runs[0]
        if history.selected is not None:
            history.decisions, history.orders, history.events = (
                repository.history_details(history.selected.id)
            )
            if (
                history.selected.execution_backend == "gtt"
                and history.selected.entry_reserved
            ):
                from app.repositories.gtt import GttRepository

                history.gtt = GttRepository().get(history.selected.id)
        return history
    except Exception:  # noqa: BLE001 -- database errors may contain sensitive SQL values
        history.message = (
            "History unavailable. Check configuration and database migrations."
        )
        return history
