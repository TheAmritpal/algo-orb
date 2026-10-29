import logging

from app.core.logging import configure_logging


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
