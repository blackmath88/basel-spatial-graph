"""Domain errors that map to clean HTTP responses instead of stack traces."""


class BaselGraphError(Exception):
    """Base class for expected, explainable failures."""

    status_code = 400
    code = "error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details

    def as_payload(self) -> dict:
        return {"error": self.code, "message": self.message, "details": self.details}


class InvalidCoordinateError(BaselGraphError):
    status_code = 422
    code = "invalid_coordinates"


class EmptyNetworkError(BaselGraphError):
    status_code = 503
    code = "empty_network"


class OutsideNetworkError(BaselGraphError):
    status_code = 422
    code = "outside_network"


class NetworkSourceError(BaselGraphError):
    """Raised by a walking-network source that could not produce a network."""

    status_code = 503
    code = "network_source_unavailable"


class ServiceSourceError(BaselGraphError):
    """Raised by a service (POI) source that could not produce locations."""

    status_code = 503
    code = "service_source_unavailable"


class UnknownCategoryError(BaselGraphError):
    status_code = 404
    code = "unknown_category"


class UnknownServiceError(BaselGraphError):
    status_code = 404
    code = "unknown_service"


class UnroutableServiceError(BaselGraphError):
    status_code = 422
    code = "unroutable_service"
