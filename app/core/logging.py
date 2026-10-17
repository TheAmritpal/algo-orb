"""Keep OAuth authorization codes out of Uvicorn access logs."""

import logging


class OAuthAccessFilter(logging.Filter):
    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            path = args[2]
            if path.startswith("/auth/upstox/"):
                record.args = (*args[:2], path.split("?", 1)[0], *args[3:])
        return True


def install_access_filter():
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, OAuthAccessFilter) for item in logger.filters):
        logger.addFilter(OAuthAccessFilter())
