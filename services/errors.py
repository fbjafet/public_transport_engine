class ServiceError(Exception):
    """A business-rule failure that maps to an HTTP status."""

    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Invalid(ServiceError):
    status_code = 400


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409
