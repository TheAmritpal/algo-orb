"""Daily token renewal at 08:30 IST, independently of strategy execution."""

import logging
import signal
from datetime import UTC, date, datetime, time, timedelta
from threading import Event

from app.services.upstox_totp import retrieve_token
from app.strategies.rules import IST

logger = logging.getLogger(__name__)


def renewal_due(now: datetime, attempted: date | None) -> bool:
    local = now.astimezone(IST)
    return time(8, 30) <= local.time() < time(15, 30) and attempted != local.date()


def run_scheduler(stop: Event, *, clock=None, renew=None) -> int:
    clock = clock or (lambda: datetime.now(UTC))
    renew = renew or retrieve_token
    attempted = None
    retry_at = None
    retry_seconds = 30
    while not stop.is_set():
        now = clock()
        if renewal_due(now, attempted) and (retry_at is None or now >= retry_at):
            try:
                result = renew()
            except Exception:  # noqa: BLE001 -- broker/DB exception text can contain secrets
                logger.error(
                    "Scheduled token renewal unavailable; retrying after backoff. "
                    "Saved credentials will be verified before another issuance attempt."
                )
                retry_at = now + timedelta(seconds=retry_seconds)
                retry_seconds = min(300, retry_seconds * 2)
            else:
                attempted = now.astimezone(IST).date()
                retry_at = None
                retry_seconds = 30
                message = {
                    "saved": "Scheduled token renewal: verified token saved. No trading started.",
                    "valid": "Scheduled token renewal: existing token is valid.",
                    "disconnected": "Scheduled token renewal skipped: account is disconnected.",
                }.get(
                    result,
                    "Scheduled token renewal returned an unexpected result; check configuration.",
                )
                logger.info(message)
        stop.wait(30)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    logger.info(
        "Token scheduler started: daily at 08:30 IST; late-start window ends at 15:30 IST."
    )
    return run_scheduler(stop)


if __name__ == "__main__":
    raise SystemExit(main())
