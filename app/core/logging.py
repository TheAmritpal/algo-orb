"""Redact OAuth codes and SDK response bodies that may contain tokens."""

import logging


class OAuthAccessFilter(logging.Filter):
    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            path = args[2]
            if path.startswith("/auth/upstox/"):
                record.args = (*args[:2], path.split("?", 1)[0], *args[3:])
        return True


class BrokerResponseFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.msg, str) and record.msg.startswith("response body:"):
            record.msg = "response body: [redacted]"
            record.args = ()
        return True


def install_access_filter():
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, OAuthAccessFilter) for item in logger.filters):
        logger.addFilter(OAuthAccessFilter())
    broker_logger = logging.getLogger("upstox_client.rest")
    if not any(
        isinstance(item, BrokerResponseFilter) for item in broker_logger.filters
    ):
        broker_logger.addFilter(BrokerResponseFilter())
