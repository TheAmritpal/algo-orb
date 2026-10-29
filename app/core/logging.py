import logging
import re

_REDACTIONS = (
    re.compile(
        r"(?i)\b(access_token|authorization|token|secret|password|code|api_key|api_secret)"
        r"(\s*[=:]\s*)([^\s,&]+)"
    ),
)

_factory_installed = False


def redact(text: str) -> str:
    for pattern in _REDACTIONS:
        text = pattern.sub(r"\1\2[REDACTED]", text)
    return text


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
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
