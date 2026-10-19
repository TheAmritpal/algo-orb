"""Persistent execution settings. Missing or invalid configuration fails closed."""

from dataclasses import dataclass

from app.repositories.database import get_connection


@dataclass(frozen=True)
class TradingConfig:
    mode: str = "paper"
    live_enabled: bool = False

    def __post_init__(self):
        if self.mode not in {"paper", "live"} or type(self.live_enabled) is not bool:
            raise ValueError("Invalid trading configuration")


def get_trading_config() -> TradingConfig:
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT mode, live_enabled FROM trading_config WHERE id = 1")
            row = cursor.fetchone()
        if row is None or row[1] not in (0, 1):
            raise ValueError("Trading configuration is missing or invalid")
        return TradingConfig(row[0], bool(row[1]))
    finally:
        connection.close()


def save_trading_config(config: TradingConfig) -> None:
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE trading_config SET mode = %s, live_enabled = %s WHERE id = 1",
                (config.mode, config.live_enabled),
            )
            # An unchanged UPDATE can report zero rows, so check existence separately.
            cursor.execute("SELECT id FROM trading_config WHERE id = 1")
            if cursor.fetchone() is None:
                raise ValueError("Trading configuration is missing")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
