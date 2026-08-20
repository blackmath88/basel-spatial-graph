"""Attaching services to the walking network, and querying what is reachable.

Snapping happens once (at preparation time, cached; or once at startup if the
network changed) and is stored as an ACCESS_POINT relation: service -> nearest
walkable node plus the snap distance. Reachability itself is never materialized
— it is recomputed per request from the Dijkstra cost map, which is a dictionary
lookup per reachable node.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

from shapely import wkt
from shapely.errors import ShapelyError

from .config import MAX_SERVICE_SNAP_M, POOR_SERVICE_SNAP_M
from .errors import UnknownCategoryError
from .service_model import (
    CATEGORY_COLORS,
    ESSENTIAL_CATEGORIES,
    ServiceCategory,
    ServiceLocation,
    category_label,
    parse_category,
)

# Vertices of a large park outline; enough to find the nearest street edge.
MAX_FOOTPRINT_VERTICES = 400


def snap_services(
    streets,
    services: Sequence[ServiceLocation],
    poor_m: float = POOR_SERVICE_SNAP_M,
    max_m: float = MAX_SERVICE_SNAP_M,
) -> Sequence[ServiceLocation]:
    """Fill in each service's access node, distance and snap quality, in place.

    Point services snap from their position. Area services (parks, sports
    grounds) snap from the nearest point of their outline — a large park's
    centre can be 200 m from any street while its gate is on the pavement.
    """
    if not services:
        return services
    points = [(s.lon, s.lat) for s in services]
    snapped = streets.nearest_nodes(points)
    for service, (node_id, distance) in zip(services, snapped):
        service.access_node_id = node_id
        service.access_distance_m = distance

    for service in services:
        outline = _footprint_points(service)
        if not outline:
            continue
        candidates = streets.nearest_nodes(outline)
        if not candidates:
            continue
        node_id, distance = min(candidates, key=lambda item: item[1])
        if distance < (service.access_distance_m or float("inf")):
            service.access_node_id = node_id
            service.access_distance_m = distance

    for service in services:
        distance = service.access_distance_m
        if distance is None:
            service.access_quality = "unsnapped"
        elif distance > max_m:
            service.access_quality = "unreachable"
            service.access_node_id = None
        elif distance > poor_m:
            service.access_quality = "poor"
        else:
            service.access_quality = "good"
    return services


def _footprint_points(service: ServiceLocation):
    if not service.footprint_wkt:
        return None
    try:
        geometry = wkt.loads(service.footprint_wkt)
    except (ShapelyError, TypeError, ValueError):
        return None
    if geometry.is_empty:
        return None
    boundary = geometry.boundary if geometry.geom_type in {"Polygon", "MultiPolygon"} else geometry
    coords = []
    for part in getattr(boundary, "geoms", [boundary]):
        coords.extend(list(part.coords))
        if len(coords) >= MAX_FOOTPRINT_VERTICES:
            break
    return coords[:MAX_FOOTPRINT_VERTICES] or None


def index_from_payload(payload: dict, streets, on_resnap=None) -> "ServiceIndex":
    """Build a ServiceIndex from a cache payload, re-snapping only if needed.

    The cache stores which walking network it was snapped against. If the
    network has since been re-prepared, snapping is redone in memory rather
    than silently pointing at node ids that no longer exist.
    """
    from .service_sources.cache import network_fingerprint

    services = payload.get("services", [])
    fingerprint = network_fingerprint(streets)
    resnapped = False
    if services and payload.get("network_fingerprint") != fingerprint:
        snap_services(streets, services)
        resnapped = True
        if on_resnap:
            on_resnap(fingerprint)
    index = ServiceIndex(
        services,
        mode=payload.get("mode", "fixture"),
        fallback_reason=payload.get("fallback_reason"),
        generated_at=payload.get("generated_at"),
        source_errors=payload.get("errors"),
    )
    index.resnapped = resnapped
    index.network_fingerprint = fingerprint
    return index


class ServiceIndex:
    """Everything the API needs to answer 'which services are reachable?'."""

    def __init__(self, services: Iterable[ServiceLocation], mode: str = "fixture",
                 fallback_reason: Optional[str] = None, generated_at: Optional[str] = None,
                 source_errors: Optional[dict] = None):
        self.services: List[ServiceLocation] = list(services)
        self.mode = mode
        self.fallback_reason = fallback_reason
        self.generated_at = generated_at
        self.source_errors = source_errors or {}
        self.resnapped = False
        self.network_fingerprint: Optional[str] = None
        self.by_id: Dict[str, ServiceLocation] = {s.id: s for s in self.services}
        self.by_category: Dict[ServiceCategory, List[ServiceLocation]] = {}
        self.access_map: Dict[str, List[ServiceLocation]] = {}
        for service in self.services:
            self.by_category.setdefault(service.category, []).append(service)
            if service.is_routable:
                self.access_map.setdefault(service.access_node_id, []).append(service)

    # -- introspection --------------------------------------------------------
    @property
    def categories(self) -> List[ServiceCategory]:
        return [c for c in ServiceCategory if c in self.by_category]

    def get(self, service_id: str) -> Optional[ServiceLocation]:
        return self.by_id.get(service_id)

    def of_category(self, category) -> List[ServiceLocation]:
        category = parse_category(category)
        if category not in self.by_category:
            raise UnknownCategoryError(
                f"No prepared services for category '{category.value}'.",
                known=[c.value for c in self.categories],
            )
        return self.by_category[category]

    def summary(self) -> dict:
        rows = []
        for category in self.categories:
            items = self.by_category[category]
            sources = sorted({s.source for s in items})
            rows.append({
                "category": category.value,
                "label": category_label(category),
                "color": CATEGORY_COLORS.get(category),
                "essential": category in ESSENTIAL_CATEGORIES,
                "count": len(items),
                "routable": sum(1 for s in items if s.is_routable),
                "unnamed": sum(1 for s in items if not s.name),
                "sources": sources,
                "datasets": sorted({s.source_dataset for s in items}),
            })
        return {
            "mode": self.mode,
            "fallback_reason": self.fallback_reason,
            "generated_at": self.generated_at,
            "resnapped_at_startup": self.resnapped,
            "total": len(self.services),
            "routable": sum(1 for s in self.services if s.is_routable),
            "essential_categories": [c.value for c in ESSENTIAL_CATEGORIES],
            "categories": rows,
            "source_errors": self.source_errors,
        }

    def feature_collection(self, categories: Optional[Sequence[ServiceCategory]] = None) -> dict:
        wanted = set(categories) if categories else None
        return {
            "type": "FeatureCollection",
            "features": [
                s.to_feature() for s in self.services
                if wanted is None or s.category in wanted
            ],
        }

    # -- the query ------------------------------------------------------------
    def reachable(self, costs: Dict[str, float], budget_m: float, speed_kmh: float,
                  categories: Optional[Sequence[ServiceCategory]] = None,
                  include_items: bool = True, limit: Optional[int] = None) -> dict:
        """Group services reachable within `budget_m` by category.

        `costs` is the Dijkstra cost map from the origin. A service's walking
        distance is its access node's cost plus its own snap distance.
        """
        wanted = tuple(categories) if categories else tuple(self.categories)
        wanted_set = set(wanted)
        metres_per_minute = speed_kmh * 1000.0 / 60.0
        found: Dict[ServiceCategory, List] = {c: [] for c in wanted}

        for node_id, cost in costs.items():
            for service in self.access_map.get(node_id, ()):
                if service.category not in wanted_set:
                    continue
                distance = cost + service.access_distance_m
                if distance <= budget_m:
                    found[service.category].append((distance, service))

        result = {}
        for category in wanted:
            rows = sorted(found[category], key=lambda pair: pair[0])
            items = [
                self._item(service, distance, metres_per_minute)
                for distance, service in (rows[:limit] if limit else rows)
            ] if include_items else []
            nearest = rows[0] if rows else None
            result[category.value] = {
                "category": category.value,
                "label": category_label(category),
                "color": CATEGORY_COLORS.get(category),
                "essential": category in ESSENTIAL_CATEGORIES,
                "count": len(rows),
                "nearest_distance_m": round(nearest[0], 1) if nearest else None,
                "nearest_minutes": round(nearest[0] / metres_per_minute, 1) if nearest else None,
                "nearest_id": nearest[1].id if nearest else None,
                "nearest_name": nearest[1].display_name if nearest else None,
                "prepared_total": len(self.by_category.get(category, [])),
                "truncated": bool(limit and len(rows) > limit),
                "items": items,
            }
        return result

    @staticmethod
    def _item(service: ServiceLocation, distance: float, metres_per_minute: float) -> dict:
        payload = service.summary()
        payload["walking_distance_m"] = round(distance, 1)
        payload["walking_time_minutes"] = round(distance / metres_per_minute, 1)
        return payload

    @staticmethod
    def completeness(reachable: dict, essential=ESSENTIAL_CATEGORIES) -> dict:
        """The deliberately simple 15-minute indicator. Not an official score."""
        present, missing = [], []
        for category in essential:
            row = reachable.get(category.value)
            (present if row and row["count"] > 0 else missing).append(category.value)
        return {
            "label": "Prototype accessibility completeness",
            "definition": (
                "A category counts as reachable if at least one prepared location of that "
                "category lies within the selected walking-time budget along the pedestrian "
                "network. It is not an official urban-quality score and is not weighted by "
                "population, opening hours, capacity or quality."
            ),
            "essential_categories": [c.value for c in essential],
            "reachable_categories": present,
            "missing_categories": missing,
            "reachable_count": len(present),
            "total": len(essential),
            "ratio": round(len(present) / len(essential), 3) if essential else None,
        }
