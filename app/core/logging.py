import logging
import re

_SECRET_KEYS = (
    "access_token|authorization|token|secret|password|code|api_key|api_secret"
)

_REDACTIONS = (
    re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9._~+/=-]+)"),
    re.compile(
        rf"(?i)((?:{_SECRET_KEYS})[\"']?\s*[:=]\s*[\"']?)([^\"'\s,&}}]+)"
    ),
)

_factory_installed = False
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def redact(text: str) -> str:
    for pattern in _REDACTIONS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def _install_record_factory() -> None:
    global _factory_installed
    if _factory_installed:
        return
    original_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = original_factory(*args, **kwargs)
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        return record

    logging.setLogRecordFactory(factory)
    _factory_installed = True


def configure_logging(level: str = "INFO") -> None:
    _install_record_factory()
    logging.basicConfig(level=level.upper(), format=_LOG_FORMAT)
    for handler in logging.getLogger().handlers:
        if type(handler).__module__.startswith("_pytest"):
            continue
        handler.setFormatter(RedactingFormatter(_LOG_FORMAT))
