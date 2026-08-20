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


class UnknownModeError(BaselGraphError):
    status_code = 404
    code = "unknown_mode"


class TransitUnavailableError(BaselGraphError):
    """Raised when transit routing is asked for but no timetable is prepared."""

    status_code = 503
    code = "transit_unavailable"


class InvalidDepartureError(BaselGraphError):
    status_code = 422
    code = "invalid_departure"


class TransitSourceError(BaselGraphError):
    """Raised by a timetable source that could not produce records."""

    status_code = 503
    code = "transit_source_unavailable"


class UnknownEntityTypeError(BaselGraphError):
    status_code = 404
    code = "unknown_entity_type"


class UnknownRelationError(BaselGraphError):
    status_code = 404
    code = "unknown_relation"


class UnknownEntityError(BaselGraphError):
    status_code = 404
    code = "unknown_entity"


class QuerySpecError(BaselGraphError):
    """The query specification could not be understood or is out of bounds."""

    status_code = 422
    code = "invalid_query"


class SpatialGraphUnavailableError(BaselGraphError):
    status_code = 503
    code = "spatial_graph_unavailable"
