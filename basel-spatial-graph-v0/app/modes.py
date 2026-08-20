"""Travel modes.

One typed vocabulary shared by the API, the services and the frontend. A mode
says *how* you move; the network it runs on and the speed it assumes are
properties of the mode, not scattered constants.
"""
from __future__ import annotations

from enum import Enum


class TravelMode(str, Enum):
    WALK = "walk"
    BIKE = "bike"
    TRANSIT = "transit"

    @classmethod
    def parse(cls, value) -> "TravelMode":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            raise ValueError(
                f"Unknown travel mode '{value}'. Known: {', '.join(m.value for m in cls)}"
            )


# Which prepared street network a mode routes on. Transit walks, so it shares
# the pedestrian network; only the ride segments come from the timetable.
NETWORK_FOR_MODE = {
    TravelMode.WALK: "walk",
    TravelMode.BIKE: "bike",
    TravelMode.TRANSIT: "walk",
}

MODE_LABELS = {
    TravelMode.WALK: "Walking",
    TravelMode.BIKE: "Cycling",
    TravelMode.TRANSIT: "Walk + Transit",
}

# Kept close to the walking blue so the map stays one visual family.
MODE_COLORS = {
    TravelMode.WALK: "#5cb3ff",
    TravelMode.BIKE: "#4ee6c0",
    TravelMode.TRANSIT: "#c792ea",
}

MODE_ORDER = (TravelMode.WALK, TravelMode.BIKE, TravelMode.TRANSIT)


def mode_label(mode: TravelMode) -> str:
    return MODE_LABELS.get(mode, mode.value.title())


def parse_mode(value) -> TravelMode:
    """`TravelMode.parse` that raises the API-mapped domain error."""
    from .errors import UnknownModeError

    try:
        return TravelMode.parse(value)
    except ValueError as exc:
        raise UnknownModeError(str(exc), known=[m.value for m in TravelMode])
