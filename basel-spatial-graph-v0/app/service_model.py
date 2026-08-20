"""The normalized service domain: one model for every everyday destination.

A `ServiceLocation` is the same shape whether it came from data.bs.ch or from
OpenStreetMap. Categories are an enum, never an ad-hoc frontend string, and the
display label is kept separate from the canonical id.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class ServiceCategory(str, Enum):
    SCHOOL = "school"
    GROCERY = "grocery"
    PHARMACY = "pharmacy"
    HEALTHCARE = "healthcare"
    PARK = "park"
    SPORT = "sport"
    LIBRARY = "library"
    CULTURE = "culture"

    @classmethod
    def parse(cls, value) -> "ServiceCategory":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            raise ValueError(
                f"Unknown service category '{value}'. Known: {', '.join(c.value for c in cls)}"
            )


# The six categories the 15-minute completeness indicator is built from.
ESSENTIAL_CATEGORIES = (
    ServiceCategory.GROCERY,
    ServiceCategory.PHARMACY,
    ServiceCategory.HEALTHCARE,
    ServiceCategory.SCHOOL,
    ServiceCategory.PARK,
    ServiceCategory.SPORT,
)

# Display only. Canonical ids above never change; these can be translated.
CATEGORY_LABELS = {
    ServiceCategory.SCHOOL: "Schools",
    ServiceCategory.GROCERY: "Groceries",
    ServiceCategory.PHARMACY: "Pharmacies",
    ServiceCategory.HEALTHCARE: "Healthcare",
    ServiceCategory.PARK: "Parks",
    ServiceCategory.SPORT: "Sport",
    ServiceCategory.LIBRARY: "Libraries",
    ServiceCategory.CULTURE: "Culture",
}

CATEGORY_COLORS = {
    ServiceCategory.SCHOOL: "#ffca4b",
    ServiceCategory.GROCERY: "#7ee787",
    ServiceCategory.PHARMACY: "#ff6b6b",
    ServiceCategory.HEALTHCARE: "#c792ea",
    ServiceCategory.PARK: "#4ec9b0",
    ServiceCategory.SPORT: "#ff9f43",
    ServiceCategory.LIBRARY: "#a3b3ff",
    ServiceCategory.CULTURE: "#ff7ac6",
}


def category_label(category: ServiceCategory) -> str:
    return CATEGORY_LABELS.get(category, category.value.title())


def parse_category(value) -> ServiceCategory:
    """`ServiceCategory.parse` that raises the API-mapped domain error."""
    from .errors import UnknownCategoryError

    try:
        return ServiceCategory.parse(value)
    except ValueError as exc:
        raise UnknownCategoryError(str(exc), known=[c.value for c in ServiceCategory])


@dataclass
class ServiceLocation:
    """One everyday destination, normalized across all providers.

    `access_node_id` / `access_distance_m` are filled in once, at preparation
    time, by snapping to the walking network. They are cached, never recomputed
    per request.
    """

    id: str
    category: ServiceCategory
    lon: float
    lat: float
    source: str
    source_dataset: str
    source_id: str
    name: Optional[str] = None          # never invented; None means unnamed upstream
    source_url: Optional[str] = None
    license: Optional[str] = None
    retrieved_at: Optional[str] = None
    attributes: dict = field(default_factory=dict)
    # Simplified WGS84 outline for area services (parks, sport grounds). Used to
    # snap from the nearest edge of the site rather than from its centre.
    footprint_wkt: Optional[str] = None
    access_node_id: Optional[str] = None
    access_distance_m: Optional[float] = None
    access_quality: str = "unsnapped"   # good | poor | unreachable | unsnapped

    # -- geometry -------------------------------------------------------------
    @property
    def geometry(self) -> dict:
        return {"type": "Point", "coordinates": [self.lon, self.lat]}

    @property
    def display_name(self) -> str:
        """For UI only. Marked as a fallback, never written back as `name`."""
        return self.name or f"{category_label(self.category).rstrip('s')} (unnamed)"

    @property
    def is_routable(self) -> bool:
        return self.access_node_id is not None and self.access_quality in {"good", "poor"}

    # -- provenance -----------------------------------------------------------
    @property
    def provenance(self) -> dict:
        return {
            "source": self.source,
            "dataset": self.source_dataset,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "license": self.license,
            "retrieved_at": self.retrieved_at,
            "category": self.category.value,
            "derived": False,
        }

    # -- serialization --------------------------------------------------------
    def to_dict(self) -> dict:
        data = asdict(self)
        data["category"] = self.category.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ServiceLocation":
        payload = dict(data)
        payload["category"] = ServiceCategory.parse(payload["category"])
        payload.pop("geometry", None)
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in payload.items() if k in known})

    def to_feature(self) -> dict:
        return {
            "type": "Feature",
            "geometry": self.geometry,
            "properties": {
                "id": self.id,
                "category": self.category.value,
                "name": self.name,
                "display_name": self.display_name,
                "source": self.source,
            },
        }

    def summary(self) -> dict:
        """The shape returned inside accessibility results and /services/{cat}/{id}."""
        return {
            "id": self.id,
            "category": self.category.value,
            "category_label": category_label(self.category),
            "name": self.name,
            "display_name": self.display_name,
            "geometry": self.geometry,
            "attributes": self.attributes,
            "access": {
                "node_id": self.access_node_id,
                "snap_distance_m": round(self.access_distance_m, 1) if self.access_distance_m is not None else None,
                "quality": self.access_quality,
            },
            "provenance": self.provenance,
        }
