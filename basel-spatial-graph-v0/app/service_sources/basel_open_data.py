"""Official Basel-Stadt service locations from data.bs.ch.

Preferred over OpenStreetMap wherever the canton publishes a suitable dataset:
schools, sport facilities (Sportamt BS), culture venues and clinics/hospitals
all come from here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from ..errors import ServiceSourceError
from ..ingest import _pick_geometry, fetch_dataset
from ..service_model import ServiceCategory, ServiceLocation
from .base import ServiceSource, normalize_name, safe_id

PORTAL = "Open Government Data Basel-Stadt (data.bs.ch)"
LICENSE = "CC BY 3.0 CH"


@dataclass(frozen=True)
class DatasetSpec:
    dataset: str
    title: str
    name_keys: Sequence[str]
    id_keys: Sequence[str]
    attribute_keys: Sequence[str]
    limit: int = 2000
    where: Optional[str] = None


# One entry per category this provider serves.
DATASETS = {
    ServiceCategory.SCHOOL: DatasetSpec(
        dataset="100029", title="Schulstandorte",
        name_keys=("sc_schulstandort",), id_keys=(),
        attribute_keys=("sc_schultyp", "sc_adresse", "mapbs_sc_link"),
    ),
    ServiceCategory.SPORT: DatasetSpec(
        dataset="100151", title="Sport- und Bewegungsanlagen",
        name_keys=("name",), id_keys=("id",),
        attribute_keys=("kategorie", "beschreibung", "strasse", "zustaendigkeit", "link"),
    ),
    ServiceCategory.CULTURE: DatasetSpec(
        dataset="100015", title="Basel Info: Interessante Orte (POI)",
        name_keys=("name",), id_keys=("tid",),
        attribute_keys=("subkatgeo", "beschreibg", "strasse", "www_link"),
        where='kategorie="Kultur & Unterhaltung"',
    ),
    ServiceCategory.HEALTHCARE: DatasetSpec(
        dataset="100015", title="Basel Info: Interessante Orte (POI)",
        name_keys=("name",), id_keys=("tid",),
        attribute_keys=("subkatgeo", "beschreibg", "strasse", "www_link"),
        where='kategorie="Gesundheit & Soziales"',
    ),
}


class BaselOpenDataServiceSource(ServiceSource):
    name = PORTAL
    license = LICENSE
    source_url = "https://data.bs.ch/"

    @property
    def categories(self) -> Sequence[ServiceCategory]:
        return tuple(DATASETS)

    def fetch(self, category: ServiceCategory):
        spec = DATASETS.get(category)
        if spec is None:
            raise ServiceSourceError(f"{self.name} has no dataset for category '{category.value}'")
        try:
            rows = fetch_dataset(spec.dataset, spec.limit, where=spec.where)
        except Exception as exc:
            raise ServiceSourceError(
                f"data.bs.ch dataset {spec.dataset} ({spec.title}) is unavailable: {exc}"
            )
        retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")
        services = []
        for index, row in enumerate(rows):
            geometry = _pick_geometry(row)
            point = _point_of(geometry)
            if point is None:
                continue
            source_id = _source_id(row, spec.id_keys, point)
            services.append(ServiceLocation(
                id=safe_id("service", category.value, "bs", spec.dataset, source_id),
                category=category,
                lon=point[0], lat=point[1],
                name=_name_of(row, spec.name_keys),
                source=PORTAL,
                source_dataset=f"{spec.dataset} — {spec.title}",
                source_id=str(source_id),
                source_url=f"https://data.bs.ch/explore/dataset/{spec.dataset}/",
                license=LICENSE,
                retrieved_at=retrieved,
                attributes={k: row[k] for k in spec.attribute_keys if row.get(k) not in (None, "")},
            ))
        if not services:
            raise ServiceSourceError(
                f"data.bs.ch dataset {spec.dataset} returned no usable geometry for '{category.value}'"
            )
        return services


def _name_of(row, keys):
    for key in keys:
        name = normalize_name(row.get(key))
        if name:
            return name
    return None


def _source_id(row, keys, point):
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
    # Datasets like Schulstandorte publish no key; derive a stable one from the
    # position so ids survive re-ingestion instead of shifting with row order.
    import hashlib

    return hashlib.sha1(f"{point[0]:.7f},{point[1]:.7f}".encode()).hexdigest()[:10]


def _point_of(geometry):
    """Everything here is already a point; polygons fall back to a centroid."""
    if not geometry:
        return None
    if geometry.get("type") == "Point":
        lon, lat = geometry["coordinates"][:2]
        return float(lon), float(lat)
    try:
        from shapely.geometry import shape

        centroid = shape(geometry).centroid
        return float(centroid.x), float(centroid.y)
    except Exception:
        return None
