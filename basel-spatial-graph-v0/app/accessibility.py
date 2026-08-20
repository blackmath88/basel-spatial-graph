"""Network-weighted accessibility over a real (or fixture) street graph.

One engine serves both street modes. Everything is measured along the graph: a
time budget becomes a distance budget at the mode's speed, Dijkstra spends that
budget on real edge lengths, and the result is the set of segments actually
reachable — on foot at 4.8 km/h, or by bicycle over the bicycle-accessible
graph at 15 km/h. The straight-line circle is computed too, but only ever
returned as a labelled comparison.

Transit builds on this rather than replacing it: see `app/multimodal.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

import networkx as nx
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union

from .config import DEFAULT_CYCLING_SPEED_KMH, DEFAULT_WALKING_SPEED_KMH, NETWORK_BUFFER_M
from .modes import NETWORK_FOR_MODE, TravelMode, mode_label
from .errors import (
    EmptyNetworkError,
    InvalidCoordinateError,
    UnknownServiceError,
    UnroutableServiceError,
)
from .projection import project_geometry, to_metric
from .service_index import ServiceIndex
from .service_model import ServiceCategory
from .street_sources import StreetNetwork

# GeoJSON output precision: ~0.1 m, well beyond what OSM geometry supports.
COORD_PRECISION = 6

# Entities may sit off the network (inside a park, a courtyard); beyond this
# they are treated as not attached rather than dragged to a distant street.
MAX_ENTITY_SNAP_M = 500.0


def _round_geometry(geometry: dict) -> dict:
    """Trim GeoJSON coordinate noise; halves the payload, changes nothing visible."""
    def walk(value):
        if isinstance(value, (list, tuple)):
            if value and isinstance(value[0], (int, float)):
                return [round(float(c), COORD_PRECISION) for c in value]
            return [walk(item) for item in value]
        return value

    return {**geometry, "coordinates": walk(geometry["coordinates"])}


class NetworkAccessibilityService:
    """Answers 'what is reachable from here?' over one street network."""

    travel_mode = TravelMode.WALK

    def __init__(
        self,
        streets: StreetNetwork,
        entity_graph: Optional[nx.MultiDiGraph] = None,
        services: Optional[ServiceIndex] = None,
        buffer_m: float = NETWORK_BUFFER_M,
        travel_mode: Optional[TravelMode] = None,
        default_speed_kmh: Optional[float] = None,
    ):
        self.travel_mode = travel_mode or type(self).travel_mode
        self.mode = self.travel_mode.value
        self.network = NETWORK_FOR_MODE[self.travel_mode]
        self.default_speed_kmh = default_speed_kmh or (
            DEFAULT_CYCLING_SPEED_KMH if self.travel_mode is TravelMode.BIKE
            else DEFAULT_WALKING_SPEED_KMH
        )
        self.streets = streets
        self.entity_graph = entity_graph if entity_graph is not None else nx.MultiDiGraph()
        self.services = services if services is not None else ServiceIndex([])
        self.buffer_m = buffer_m
        self._entity_access = {}
        self._components = {}
        self._attach_entities()
        self._label_components()

    # -- setup ----------------------------------------------------------------
    @property
    def label(self) -> str:
        return mode_label(self.travel_mode)

    def _attach_entities(self) -> None:
        """Precompute each point entity's nearest network node, once."""
        entities = [
            (node_id, data["geometry"]["coordinates"])
            for node_id, data in self.entity_graph.nodes(data=True)
            if data.get("type") in {"School", "Accident"}
            and isinstance(data.get("geometry"), dict)
            and data["geometry"].get("type") == "Point"
        ]
        if not entities:
            return
        snapped = self.streets.nearest_nodes([coords for _, coords in entities])
        for (node_id, _), (access_node, distance) in zip(entities, snapped):
            if distance <= MAX_ENTITY_SNAP_M:
                self._entity_access[node_id] = (access_node, distance)

    def _label_components(self) -> None:
        """Disconnected network fragments are normal; make them reportable."""
        for index, component in enumerate(nx.connected_components(self.streets.graph)):
            size = len(component)
            for node in component:
                self._components[node] = (index, size)

    # -- model ----------------------------------------------------------------
    @staticmethod
    def budget_m(minutes: float, speed_kmh: float) -> float:
        """Time budget -> distance budget: metres = km/h * 1000 * min / 60."""
        return speed_kmh * 1000.0 * minutes / 60.0

    # -- query ----------------------------------------------------------------
    def calculate(
        self,
        lat: float,
        lon: float,
        minutes: float = 15.0,
        speed_kmh: Optional[float] = None,
        include_straight_line: bool = True,
        include_buffer: bool = False,
        categories: Optional[Sequence[ServiceCategory]] = None,
        include_services: bool = True,
        include_service_items: bool = True,
        service_limit: Optional[int] = None,
        include_geometry: bool = True,
    ) -> dict:
        minutes = self._positive(minutes, "minutes")
        speed_kmh = self._positive(
            self.default_speed_kmh if speed_kmh is None else speed_kmh, "speed_kmh")
        if self.streets.graph.number_of_nodes() == 0:
            raise EmptyNetworkError("The walking network contains no nodes.")

        origin_node, snap_m = self.streets.nearest_node(lat, lon)
        budget = self.budget_m(minutes, speed_kmh)
        costs = nx.single_source_dijkstra_path_length(
            self.streets.graph, origin_node, cutoff=budget, weight="length_m"
        )

        edges, geoms, total_length = self._reachable_edges(costs, budget)
        origin_data = self.streets.graph.nodes[origin_node]
        component_index, component_size = self._components.get(origin_node, (None, 1))

        features = []
        if include_geometry:
            for u, v, data in edges:
                feature = self.streets.edge_feature(u, v, data)
                feature["properties"]["kind"] = "reachable_edge"
                features.append(feature)
            if include_buffer:
                boundary = self._network_buffer(geoms)
                if boundary is not None:
                    features.append(boundary)
            if include_straight_line:
                features.append(self._straight_line_circle(lon, lat, budget))

        if include_services:
            reachable_services = self.services.reachable(
                costs, budget, speed_kmh, categories=categories,
                include_items=include_service_items, limit=service_limit,
                network=self.network,
            )
        else:
            reachable_services = {}
        completeness = ServiceIndex.completeness(reachable_services) if include_services else None

        accident_count = self._reachable_accidents(costs, budget)
        areas = self._reachable_areas(geoms) if include_geometry else []

        notes = []
        if not edges:
            notes.append(
                "The snapped node has no walkable edge within the time budget; "
                "it is probably an isolated network fragment."
            )
        if component_size < 10:
            notes.append(
                f"The origin sits in a small disconnected network fragment ({component_size} nodes)."
            )

        return {
            "origin": {"lat": round(float(lat), 6), "lon": round(float(lon), 6)},
            "snapped_origin": {
                "node_id": origin_node,
                "lat": round(float(origin_data["lat"]), 6),
                "lon": round(float(origin_data["lon"]), 6),
                "snap_distance_m": round(snap_m, 1),
                "component_size": component_size,
                "component_index": component_index,
            },
            "mode": self.mode,
            "mode_label": self.label,
            "minutes": minutes,
            "speed_kmh": speed_kmh,
            # V0.2/V0.3 name, kept for existing clients.
            "walking_speed_kmh": speed_kmh,
            "network": {
                "origin_node_id": origin_node,
                "reachable_node_count": len(costs),
                "reachable_edge_count": len(edges),
                "reachable_edge_length_m": round(total_length, 1),
                "distance_budget_m": round(budget, 1),
                "max_network_distance_m": round(max(costs.values(), default=0.0), 1),
            },
            "geometry": {"type": "FeatureCollection", "features": features},
            "reachable_services": reachable_services,
            "completeness": completeness,
            "reachable_entities": {
                "accident_count": accident_count,
                "areas": areas,
            },
            "euclidean_vs_network": self._comparisons(lon, lat, reachable_services),
            "notes": notes,
            "provenance": {
                "travel_mode": self.mode,
                "network_kind": self.network,
                "network_source": self.streets.source_name,
                "mode": self.streets.mode,
                "classification": "analytical result",
                "algorithm": "NetworkX single-source Dijkstra",
                "routing_method": f"network distance / {speed_kmh:g} km/h",
                "edge_weight": "length_m",
                "distance_crs": self.streets.provenance.get("metric_crs"),
                "speed_kmh": speed_kmh,
                "walking_speed_kmh": speed_kmh,
                "time_budget_minutes": minutes,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "network": self.streets.provenance,
                "fallback_reason": self.streets.fallback_reason,
                "services_mode": self.services.mode,
                "services_fallback_reason": self.services.fallback_reason,
            },
        }

    def route_to_service(self, lat: float, lon: float, service_id: str,
                         speed_kmh: Optional[float] = None) -> dict:
        """The shortest walking path from a clicked origin to one service.

        Same graph, same weights as the reachability query, so the reported
        distance always agrees with the profile.
        """
        speed_kmh = self._positive(
            self.default_speed_kmh if speed_kmh is None else speed_kmh, "speed_kmh")
        service = self.services.get(service_id)
        if service is None:
            raise UnknownServiceError(f"No prepared service with id '{service_id}'.")
        access = service.access_for(self.network)
        if not access.is_routable:
            raise UnroutableServiceError(
                f"'{service.display_name}' is {access.distance_m:.0f} m from the nearest "
                f"{self.network} street and is not attached to that network."
                if access.distance_m is not None else
                f"'{service.display_name}' is not attached to the {self.network} network.",
                service_id=service_id, access_quality=access.quality, network=self.network,
            )
        origin_node, snap_m = self.streets.nearest_node(lat, lon)
        try:
            path = nx.shortest_path(self.streets.graph, origin_node, access.node_id,
                                    weight="length_m")
        except nx.NetworkXNoPath:
            raise UnroutableServiceError(
                f"No walking route from here to '{service.display_name}'; they are in "
                "disconnected parts of the network.",
                service_id=service_id,
            )
        lines, network_distance = [], 0.0
        for u, v in zip(path, path[1:]):
            data = self.streets.graph[u][v]
            lines.append(data["geom"])
            network_distance += data["length_m"]
        total = network_distance + access.distance_m
        metres_per_minute = speed_kmh * 1000.0 / 60.0
        features = []
        if lines:
            features.append({
                "type": "Feature",
                "geometry": _round_geometry(mapping(unary_union(lines))),
                "properties": {"kind": "route", "length_m": round(network_distance, 1)},
            })
        node = self.streets.graph.nodes[access.node_id]
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [
                [round(node["lon"], 6), round(node["lat"], 6)],
                [round(service.lon, 6), round(service.lat, 6)],
            ]},
            "properties": {"kind": "route_connector",
                           "length_m": round(access.distance_m, 1)},
        })
        return {
            "origin": {"lat": round(float(lat), 6), "lon": round(float(lon), 6)},
            "snapped_origin": {"node_id": origin_node, "snap_distance_m": round(snap_m, 1)},
            "service": service.summary(self.network),
            "mode": self.mode,
            "mode_label": self.label,
            "speed_kmh": speed_kmh,
            "walking_speed_kmh": speed_kmh,
            "network_distance_m": round(network_distance, 1),
            "walking_distance_m": round(total, 1),
            "walking_time_minutes": round(total / metres_per_minute, 1),
            "node_count": len(path),
            "geometry": {"type": "FeatureCollection", "features": features},
            "provenance": {
                "travel_mode": self.mode,
                "network_kind": self.network,
                "algorithm": "NetworkX Dijkstra shortest path",
                "edge_weight": "length_m",
                "distance_crs": self.streets.provenance.get("metric_crs"),
                "network_source": self.streets.source_name,
                "mode": self.streets.mode,
                "speed_kmh": speed_kmh,
                "service": service.provenance,
            },
        }

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _positive(value, field):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise InvalidCoordinateError(f"{field} must be a number")
        if not (number > 0) or number != number:
            raise InvalidCoordinateError(f"{field} must be greater than zero")
        return number

    def _reachable_edges(self, costs, budget):
        """Only walk edges incident to reachable nodes, not the whole city."""
        edges, geoms, total = [], [], 0.0
        seen = set()
        graph = self.streets.graph
        for u in costs:
            for v, data in graph[u].items():
                if v not in costs:
                    continue
                key = (u, v) if str(u) <= str(v) else (v, u)
                if key in seen:
                    continue
                seen.add(key)
                if max(costs[u], costs[v]) > budget + 1e-9:
                    continue
                edges.append((key[0], key[1], data))
                geoms.append(data["geom"])
                total += data["length_m"]
        return edges, geoms, total

    def _network_buffer(self, geoms):
        """Opt-in visual aid: the reachable network widened by a fixed corridor.

        Buffering a dense city network in GEOS costs ~0.7 s, so the map draws
        the same corridor as a wide translucent line instead. This exists for
        API consumers that need an actual polygon.
        """
        if not geoms:
            return None
        merged = project_geometry(unary_union(geoms)).simplify(self.buffer_m / 6.0)
        polygon = merged.buffer(self.buffer_m, resolution=2)
        return {
            "type": "Feature",
            "geometry": _round_geometry(mapping(project_geometry(polygon, inverse=True))),
            "properties": {
                "kind": "network_buffer",
                "approximate": True,
                "method": f"reachable network buffered by {self.buffer_m:.0f} m in {self.streets.provenance.get('metric_crs')}",
            },
        }

    def _straight_line_circle(self, lon, lat, budget):
        """The naive 'as the crow flies' answer, for comparison only."""
        x, y = to_metric(lon, lat)
        circle = Point(float(x), float(y)).buffer(budget, resolution=16)
        return {
            "type": "Feature",
            "geometry": _round_geometry(mapping(project_geometry(circle, inverse=True))),
            "properties": {
                "kind": "straight_line_radius",
                "radius_m": round(budget, 1),
                "method": "Euclidean radius — NOT reachability; shown for comparison",
            },
        }

    def _reachable_accidents(self, costs, budget):
        """Accidents stay context, not a destination category."""
        count = 0
        for entity_id, (access_node, connector) in self._entity_access.items():
            if access_node not in costs:
                continue
            if costs[access_node] + connector > budget:
                continue
            if self.entity_graph.nodes[entity_id].get("type") == "Accident":
                count += 1
        return count

    def _reachable_areas(self, geoms):
        if not geoms:
            return []
        reachable = unary_union(geoms)
        areas = []
        for entity_id, entity in self.entity_graph.nodes(data=True):
            if entity.get("type") != "Area" or not isinstance(entity.get("geometry"), dict):
                continue
            try:
                if shape(entity["geometry"]).intersects(reachable):
                    areas.append({
                        "id": entity_id,
                        "name": entity.get("name"),
                        "method": "polygon intersects reachable street segments",
                    })
            except Exception:
                continue
        return areas

    def _comparisons(self, lon, lat, reachable_services):
        """Nearest service per category: how much longer is the walk than the crow's flight?"""
        from .graph import haversine_m

        rows = []
        for category, row in reachable_services.items():
            service = self.services.get(row.get("nearest_id")) if row.get("nearest_id") else None
            if service is None:
                continue
            euclidean = haversine_m((lon, lat), (service.lon, service.lat))
            network = row["nearest_distance_m"]
            rows.append({
                "category": category,
                "label": row["label"],
                "id": service.id,
                "name": service.display_name,
                "euclidean_distance_m": round(euclidean, 1),
                "network_distance_m": network,
                "network_detour_factor": round(network / euclidean, 2) if euclidean else 1.0,
            })
        rows.sort(key=lambda r: r["category"])
        return rows


class WalkingAccessibilityService(NetworkAccessibilityService):
    """Walking. The V0.2/V0.3 class name and constructor, unchanged."""

    travel_mode = TravelMode.WALK


class CyclingAccessibilityService(NetworkAccessibilityService):
    """Cycling over the bicycle-accessible network.

    Deliberately the same engine as walking with a different graph and speed:
    for a prototype, `edge length / 15 km/h` is the whole cost model. It ignores
    slope, traffic stress, surface, turn penalties and one-way rules — see
    docs/CYCLING.md.
    """

    travel_mode = TravelMode.BIKE
