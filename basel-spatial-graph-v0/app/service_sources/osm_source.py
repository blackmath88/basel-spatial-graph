"""OpenStreetMap service locations via OSMnx.

Used for the categories Basel-Stadt does not publish itself: groceries,
pharmacies, doctors' practices and public green space. Downloads only ever
happen during `python -m app.prepare_data`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from ..config import BASEL_PLACE_QUERIES, OSMNX_CACHE_DIR
from ..errors import ServiceSourceError
from ..service_model import ServiceCategory, ServiceLocation
from .base import ServiceSource, normalize_name, safe_id

OSM_LICENSE = "ODbL 1.0"
OSM_URL = "https://www.openstreetmap.org/copyright"
# Area services below this are green strips and traffic islands, not parks.
MIN_AREA_M2 = 500.0
# Sites tagged as closed to the public are not everyday destinations.
CLOSED_ACCESS = {"private", "no", "permit"}
# Polygon outlines are stored simplified; ~5 m is far below walking precision.
FOOTPRINT_SIMPLIFY_M = 5.0


@dataclass(frozen=True)
class TagSpec:
    tags: dict
    description: str
    keep_keys: Sequence[str] = ()
    min_area_m2: Optional[float] = None


TAGS = {
    ServiceCategory.GROCERY: TagSpec(
        tags={"shop": ["supermarket", "convenience", "greengrocer", "grocery"]},
        description="shop=supermarket|convenience|greengrocer|grocery",
        keep_keys=("shop", "brand", "operator", "opening_hours", "organic"),
    ),
    ServiceCategory.PHARMACY: TagSpec(
        tags={"amenity": ["pharmacy"]},
        description="amenity=pharmacy",
        keep_keys=("amenity", "operator", "opening_hours", "dispensing"),
    ),
    ServiceCategory.HEALTHCARE: TagSpec(
        tags={"amenity": ["doctors", "clinic", "hospital"],
              "healthcare": ["doctor", "centre", "general"]},
        description="amenity=doctors|clinic|hospital, healthcare=doctor|centre|general",
        keep_keys=("amenity", "healthcare", "healthcare:speciality", "operator"),
    ),
    ServiceCategory.PARK: TagSpec(
        tags={"leisure": ["park"], "landuse": ["village_green", "recreation_ground"]},
        description="leisure=park, landuse=village_green|recreation_ground",
        keep_keys=("leisure", "landuse", "access", "operator"),
        min_area_m2=MIN_AREA_M2,
    ),
    ServiceCategory.LIBRARY: TagSpec(
        tags={"amenity": ["library"]},
        description="amenity=library",
        keep_keys=("amenity", "operator", "opening_hours"),
    ),
}


class OSMServiceSource(ServiceSource):
    name = "OpenStreetMap"
    license = OSM_LICENSE
    source_url = OSM_URL

    def __init__(self, place_queries: Sequence[str] = BASEL_PLACE_QUERIES):
        self.place_queries = tuple(place_queries)

    @property
    def categories(self) -> Sequence[ServiceCategory]:
        return tuple(TAGS)

    def fetch(self, category: ServiceCategory):
        spec = TAGS.get(category)
        if spec is None:
            raise ServiceSourceError(f"OpenStreetMap tag mapping missing for '{category.value}'")
        ox = self._import_osmnx()
        OSMNX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ox.settings.use_cache = True
        ox.settings.cache_folder = str(OSMNX_CACHE_DIR)
        ox.settings.log_console = False

        errors = []
        for place in self.place_queries:
            try:
                frame = ox.features_from_place(place, spec.tags)
            except Exception as exc:
                errors.append(f"place '{place}': {exc}")
                continue
            services = self._convert(frame, category, spec, place)
            if services:
                return services
            errors.append(f"place '{place}': no usable features")
        raise ServiceSourceError(
            f"OpenStreetMap returned no '{category.value}' locations for Basel.", attempts=errors
        )

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _import_osmnx():
        try:
            import osmnx as ox
        except ImportError as exc:
            raise ServiceSourceError(
                "OSMnx is not installed. Install it with `pip install -r requirements.txt` "
                "to prepare live service data.",
                import_error=str(exc),
            )
        return ox

    def _convert(self, frame, category, spec: TagSpec, place: str):
        from ..config import METRIC_CRS

        retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")
        projected = frame.to_crs(METRIC_CRS)
        services = []
        for (element, osm_id), row in frame.iterrows():
            geometry = row.geometry
            if geometry is None or geometry.is_empty:
                continue
            metric = projected.geometry.loc[(element, osm_id)]
            area = float(metric.area)
            if spec.min_area_m2 is not None:
                if geometry.geom_type == "Point" or area < spec.min_area_m2:
                    continue
                if str(row.get("access") or "").lower() in CLOSED_ACCESS:
                    continue
            # A representative point is guaranteed to lie inside the shape;
            # a centroid is not, for an L-shaped park.
            point = geometry.representative_point()
            attributes = {
                key: str(row[key]) for key in spec.keep_keys
                if key in row.index and row[key] == row[key] and row[key] not in (None, "")
            }
            if geometry.geom_type != "Point":
                attributes["area_m2"] = round(area)
            services.append(ServiceLocation(
                id=safe_id("service", category.value, "osm", element, osm_id),
                category=category,
                lon=float(point.x), lat=float(point.y),
                name=normalize_name(row.get("name")),
                source=self.name,
                source_dataset=f"OSM {spec.description}",
                source_id=f"{element}/{osm_id}",
                source_url=f"https://www.openstreetmap.org/{element}/{osm_id}",
                license=OSM_LICENSE,
                retrieved_at=retrieved,
                attributes=attributes,
                footprint_wkt=self._footprint(geometry, metric),
            ))
        return services

    @staticmethod
    def _footprint(geometry, metric) -> Optional[str]:
        """Keep the outline of area services so snapping can use their edge."""
        if geometry.geom_type == "Point":
            return None
        try:
            from ..projection import project_geometry

            simplified = metric.simplify(FOOTPRINT_SIMPLIFY_M)
            return project_geometry(simplified, inverse=True).wkt
        except Exception:
            return geometry.wkt
