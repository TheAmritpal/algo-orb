class AppError(Exception):
    """Base class for errors that are safe to show to an operator."""

    status_code = 500
    public_message = "Something went wrong."

    def __init__(self, message: str | None = None) -> None:
        self.public_message = message or type(self).public_message
        super().__init__(self.public_message)


class ConfigError(AppError):
    status_code = 500
    public_message = "Invalid configuration."


class ValidationError(AppError):
    status_code = 422
    public_message = "Invalid request."


class AuthError(AppError):
    status_code = 401
    public_message = "Authentication failed."


class UpstoxOAuthError(AppError):
    status_code = 502
    public_message = "Upstox connection failed."


class BrokerRejected(AppError):
    status_code = 502
    public_message = "Broker rejected the order."


class BrokerUnavailable(AppError):
    status_code = 503
    public_message = "Broker is unavailable."


class BrokerUnknown(AppError):
    status_code = 502
    public_message = "Broker order state is unknown."
