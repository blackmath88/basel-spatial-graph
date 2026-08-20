"""The normalized service domain: one model for every everyday destination.

A `ServiceLocation` is the same shape whether it came from data.bs.ch or from
OpenStreetMap. Categories are an enum, never an ad-hoc frontend string, and the
display label is kept separate from the canonical id.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, Optional


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


# The default network a bare `access_*` property refers to.
DEFAULT_NETWORK = "walk"


@dataclass
class ServiceAccess:
    """How one service attaches to one street network."""

    node_id: Optional[str] = None
    distance_m: Optional[float] = None
    quality: str = "unsnapped"   # good | poor | unreachable | unsnapped

    @property
    def is_routable(self) -> bool:
        return self.node_id is not None and self.quality in {"good", "poor"}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> "ServiceAccess":
        if data is None:
            return cls()
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in dict(data).items() if k in known})


@dataclass
class ServiceLocation:
    """One everyday destination, normalized across all providers.

    `access` holds one `ServiceAccess` per prepared street network (`walk`,
    `bike`), each filled in once at preparation time and cached — never
    recomputed per request. A service can be well attached to the cycling
    network and badly attached to the pedestrian one, or vice versa.
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
    access: Dict[str, ServiceAccess] = field(default_factory=dict)

    # -- geometry -------------------------------------------------------------
    @property
    def geometry(self) -> dict:
        return {"type": "Point", "coordinates": [self.lon, self.lat]}

    @property
    def display_name(self) -> str:
        """For UI only. Marked as a fallback, never written back as `name`."""
        return self.name or f"{category_label(self.category).rstrip('s')} (unnamed)"

    # -- network attachment ---------------------------------------------------
    def access_for(self, network: str = DEFAULT_NETWORK) -> ServiceAccess:
        return self.access.get(network) or ServiceAccess()

    def set_access(self, network: str, node_id, distance_m, quality: str) -> None:
        self.access[network] = ServiceAccess(node_id, distance_m, quality)

    def is_routable_on(self, network: str = DEFAULT_NETWORK) -> bool:
        return self.access_for(network).is_routable

    @property
    def access_node_id(self) -> Optional[str]:
        return self.access_for().node_id

    @property
    def access_distance_m(self) -> Optional[float]:
        return self.access_for().distance_m

    @property
    def access_quality(self) -> str:
        return self.access_for().quality

    @property
    def is_routable(self) -> bool:
        """Routable on the pedestrian network — the historical meaning."""
        return self.is_routable_on(DEFAULT_NETWORK)

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
        data["access"] = {name: access.to_dict() for name, access in self.access.items()}
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ServiceLocation":
        payload = dict(data)
        payload["category"] = ServiceCategory.parse(payload["category"])
        payload.pop("geometry", None)
        access = payload.pop("access", None)
        # A V0.3 cache stored a single flat walking attachment.
        legacy = {
            "node_id": payload.pop("access_node_id", None),
            "distance_m": payload.pop("access_distance_m", None),
            "quality": payload.pop("access_quality", "unsnapped"),
        }
        known = set(cls.__dataclass_fields__)
        service = cls(**{k: v for k, v in payload.items() if k in known})
        if isinstance(access, dict):
            service.access = {name: ServiceAccess.from_dict(value) for name, value in access.items()}
        elif legacy["node_id"] is not None:
            service.access = {DEFAULT_NETWORK: ServiceAccess.from_dict(legacy)}
        return service

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

    def summary(self, network: str = DEFAULT_NETWORK) -> dict:
        """The shape returned inside accessibility results and /services/{cat}/{id}.

        `access` describes the attachment on the network the query used;
        `access_by_network` shows every prepared attachment.
        """
        def describe(item: ServiceAccess) -> dict:
            return {
                "node_id": item.node_id,
                "snap_distance_m": round(item.distance_m, 1) if item.distance_m is not None else None,
                "quality": item.quality,
            }

        return {
            "id": self.id,
            "category": self.category.value,
            "category_label": category_label(self.category),
            "name": self.name,
            "display_name": self.display_name,
            "geometry": self.geometry,
            "attributes": self.attributes,
            "access": describe(self.access_for(network)),
            "access_network": network,
            "access_by_network": {name: describe(item) for name, item in self.access.items()},
            "provenance": self.provenance,
        }
