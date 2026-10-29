import logging
import sys

from app.core.logging import RedactingFormatter, configure_logging


def test_redacts_secret_fields(caplog):
    caplog.set_level("INFO")
    configure_logging("INFO")
    logging.getLogger("test").info(
        "callback code=%s token=%s", "SECRET-CODE", "SECRET-TOKEN"
    )
    text = caplog.text
    assert "SECRET-CODE" not in text
    assert "SECRET-TOKEN" not in text
    assert "[REDACTED]" in text


def test_redacts_json_and_dict_payloads(caplog):
    caplog.set_level("INFO")
    configure_logging("INFO")
    logging.getLogger("test").info(
        "payload={'access_token': 'SECRET-A'} response={\"access_token\": \"SECRET-B\"}"
    )
    assert "SECRET-A" not in caplog.text
    assert "SECRET-B" not in caplog.text


def test_redacts_bearer_tokens(caplog):
    caplog.set_level("INFO")
    configure_logging("INFO")
    logging.getLogger("test").info("Authorization: Bearer SECRET-C")
    assert "SECRET-C" not in caplog.text


def test_formatter_redacts_tracebacks():
    formatter = RedactingFormatter("%(message)s")
    try:
        raise RuntimeError("upstream said access_token=SECRET-D")
    except RuntimeError:
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    output = formatter.format(record)
    assert "SECRET-D" not in output
    assert "[REDACTED]" in output
