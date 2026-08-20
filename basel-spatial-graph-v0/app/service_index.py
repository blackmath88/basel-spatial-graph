"""Attaching services to street networks, and querying what is reachable.

Snapping happens once (at preparation time, cached; or once at startup if a
network changed) and is stored as an ACCESS_POINT relation per network:
service -> nearest usable node on that network, plus the snap distance. A
service therefore has a WALK access point and a BIKE access point, which are
often different nodes.

Reachability itself is never materialized — it is recomputed per request from
the Dijkstra cost map, which is a dictionary lookup per reachable node.
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
    network: Optional[str] = None,
) -> Sequence[ServiceLocation]:
    """Fill in each service's access node, distance and snap quality, in place.

    Point services snap from their position. Area services (parks, sports
    grounds) snap from the nearest point of their outline — a large park's
    centre can be 200 m from any street while its gate is on the pavement.

    `network` defaults to whichever network was handed in (`streets.kind`), so
    calling this once per prepared network fills in one access point each.
    """
    if not services:
        return services
    network = network or getattr(streets, "kind", "walk")

    points = [(s.lon, s.lat) for s in services]
    best = dict(zip((s.id for s in services), streets.nearest_nodes(points)))

    for service in services:
        outline = _footprint_points(service)
        if not outline:
            continue
        candidates = streets.nearest_nodes(outline)
        if not candidates:
            continue
        node_id, distance = min(candidates, key=lambda item: item[1])
        if distance < best[service.id][1]:
            best[service.id] = (node_id, distance)

    for service in services:
        node_id, distance = best[service.id]
        if distance is None:
            service.set_access(network, None, None, "unsnapped")
        elif distance > max_m:
            service.set_access(network, None, distance, "unreachable")
        elif distance > poor_m:
            service.set_access(network, node_id, distance, "poor")
        else:
            service.set_access(network, node_id, distance, "good")
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


def index_from_payload(payload: dict, networks: dict, on_resnap=None) -> "ServiceIndex":
    """Build a ServiceIndex from a cache payload, re-snapping only if needed.

    The cache stores which networks it was snapped against, by fingerprint. If
    one has since been re-prepared — or a network was added that the cache never
    saw — snapping is redone in memory for that network only, rather than
    silently pointing at node ids that no longer exist.
    """
    from .service_sources.cache import network_fingerprints

    services = payload.get("services", [])
    fingerprints = network_fingerprints(networks)
    cached = payload.get("network_fingerprints") or _legacy_fingerprints(payload)
    resnapped = []
    for name, streets in networks.items():
        if not services:
            continue
        if cached.get(name) != fingerprints.get(name):
            snap_services(streets, services, network=name)
            resnapped.append(name)
    if resnapped and on_resnap:
        on_resnap(fingerprints, resnapped)
    index = ServiceIndex(
        services,
        mode=payload.get("mode", "fixture"),
        fallback_reason=payload.get("fallback_reason"),
        generated_at=payload.get("generated_at"),
        source_errors=payload.get("errors"),
        networks=tuple(networks),
    )
    index.resnapped = tuple(resnapped)
    index.network_fingerprints = fingerprints
    return index


def _legacy_fingerprints(payload: dict) -> dict:
    """A V0.3 cache recorded one fingerprint, for the walking network."""
    single = payload.get("network_fingerprint")
    return {"walk": single} if single else {}


class ServiceIndex:
    """Everything the API needs to answer 'which services are reachable?'."""

    def __init__(self, services: Iterable[ServiceLocation], mode: str = "fixture",
                 fallback_reason: Optional[str] = None, generated_at: Optional[str] = None,
                 source_errors: Optional[dict] = None, networks: Sequence[str] = ("walk",)):
        self.services: List[ServiceLocation] = list(services)
        self.mode = mode
        self.fallback_reason = fallback_reason
        self.generated_at = generated_at
        self.source_errors = source_errors or {}
        self.resnapped: Sequence[str] = ()
        self.network_fingerprints: Dict[str, str] = {}
        self.networks = tuple(networks)
        self.by_id: Dict[str, ServiceLocation] = {s.id: s for s in self.services}
        self.by_category: Dict[ServiceCategory, List[ServiceLocation]] = {}
        # One access map per network: node id -> the services attached to it.
        self.access_maps: Dict[str, Dict[str, List[ServiceLocation]]] = {
            name: {} for name in self.networks
        }
        for service in self.services:
            self.by_category.setdefault(service.category, []).append(service)
            for name in self.networks:
                access = service.access_for(name)
                if access.is_routable:
                    self.access_maps[name].setdefault(access.node_id, []).append(service)

    @property
    def access_map(self) -> Dict[str, List[ServiceLocation]]:
        """The pedestrian access map — the historical name."""
        return self.access_maps.get("walk", {})

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
                "routable_by_network": {
                    name: sum(1 for s in items if s.is_routable_on(name)) for name in self.networks
                },
                "unnamed": sum(1 for s in items if not s.name),
                "sources": sources,
                "datasets": sorted({s.source_dataset for s in items}),
            })
        return {
            "mode": self.mode,
            "fallback_reason": self.fallback_reason,
            "generated_at": self.generated_at,
            "resnapped_at_startup": list(self.resnapped),
            "networks": list(self.networks),
            "total": len(self.services),
            "routable": sum(1 for s in self.services if s.is_routable),
            "routable_by_network": {
                name: sum(1 for s in self.services if s.is_routable_on(name)) for name in self.networks
            },
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
                  include_items: bool = True, limit: Optional[int] = None,
                  network: str = "walk") -> dict:
        """Group services reachable within `budget_m` by category.

        `costs` is the Dijkstra cost map from the origin over `network`. A
        service's travel distance is its access node's cost plus its own snap
        distance on that same network.
        """
        wanted = tuple(categories) if categories else tuple(self.categories)
        wanted_set = set(wanted)
        metres_per_minute = speed_kmh * 1000.0 / 60.0
        found: Dict[ServiceCategory, List] = {c: [] for c in wanted}
        access_map = self.access_maps.get(network, {})

        for node_id, cost in costs.items():
            for service in access_map.get(node_id, ()):
                if service.category not in wanted_set:
                    continue
                distance = cost + service.access_for(network).distance_m
                if distance <= budget_m:
                    found[service.category].append((distance, service))

        result = {}
        for category in wanted:
            rows = sorted(found[category], key=lambda pair: pair[0])
            items = [
                self._item(service, distance, metres_per_minute, network)
                for distance, service in (rows[:limit] if limit else rows)
            ] if include_items else []
            nearest = rows[0] if rows else None
            result[category.value] = self.category_row(
                category, len(rows),
                nearest_seconds=(nearest[0] / metres_per_minute) * 60 if nearest else None,
                nearest_service=nearest[1] if nearest else None,
                items=items,
                ids=[service.id for _, service in rows],
                truncated=bool(limit and len(rows) > limit),
                extra={"nearest_distance_m": round(nearest[0], 1) if nearest else None},
            )
        return result

    def category_row(self, category, count, nearest_seconds=None, nearest_service=None,
                     items=None, ids=None, truncated=False, extra=None) -> dict:
        """The one shape every mode reports a category in.

        `ids` lists every reachable service so a map can highlight all of them;
        `items` carries the detailed rows, which may be capped for payload size.
        """
        row = {
            "category": category.value,
            "label": category_label(category),
            "color": CATEGORY_COLORS.get(category),
            "essential": category in ESSENTIAL_CATEGORIES,
            "count": count,
            "nearest_minutes": round(nearest_seconds / 60.0, 1) if nearest_seconds is not None else None,
            "nearest_id": nearest_service.id if nearest_service is not None else None,
            "nearest_name": nearest_service.display_name if nearest_service is not None else None,
            "prepared_total": len(self.by_category.get(category, [])),
            "truncated": truncated,
            "ids": ids or [],
            "items": items or [],
            "nearest_distance_m": None,
        }
        row.update(extra or {})
        return row

    @staticmethod
    def _item(service: ServiceLocation, distance: float, metres_per_minute: float,
              network: str = "walk") -> dict:
        payload = service.summary(network)
        payload["travel_distance_m"] = round(distance, 1)
        payload["travel_time_minutes"] = round(distance / metres_per_minute, 1)
        # V0.3 names, kept so existing clients and the map keep working.
        payload["walking_distance_m"] = payload["travel_distance_m"]
        payload["walking_time_minutes"] = payload["travel_time_minutes"]
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
