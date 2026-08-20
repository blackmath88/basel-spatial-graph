"""Network-weighted walking accessibility over a real (or fixture) street graph.

Everything here is measured along the graph: a time budget becomes a distance
budget, Dijkstra spends that budget on real edge lengths, and the result is the
set of street segments actually reachable on foot. The straight-line circle is
computed too, but only ever returned as a labelled comparison.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import networkx as nx
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union

from .config import DEFAULT_WALKING_SPEED_KMH, NETWORK_BUFFER_M
from .errors import EmptyNetworkError, InvalidCoordinateError
from .projection import project_geometry, to_metric
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


class WalkingAccessibilityService:
    """Answers 'what is reachable on foot from here?' for one street network."""

    mode = "walk"

    def __init__(
        self,
        streets: StreetNetwork,
        entity_graph: Optional[nx.MultiDiGraph] = None,
        buffer_m: float = NETWORK_BUFFER_M,
    ):
        self.streets = streets
        self.entity_graph = entity_graph if entity_graph is not None else nx.MultiDiGraph()
        self.buffer_m = buffer_m
        self._entity_access = {}
        self._components = {}
        self._attach_entities()
        self._label_components()

    # -- setup ----------------------------------------------------------------
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
        speed_kmh: float = DEFAULT_WALKING_SPEED_KMH,
        include_straight_line: bool = True,
        include_buffer: bool = False,
    ) -> dict:
        minutes = self._positive(minutes, "minutes")
        speed_kmh = self._positive(speed_kmh, "walking_speed_kmh")
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
        for u, v, data in edges:
            feature = self.streets.edge_feature(u, v, data)
            feature["geometry"] = _round_geometry(feature["geometry"])
            feature["properties"]["kind"] = "reachable_edge"
            features.append(feature)
        if include_buffer:
            boundary = self._network_buffer(geoms)
            if boundary is not None:
                features.append(boundary)
        if include_straight_line:
            features.append(self._straight_line_circle(lon, lat, budget))

        schools, accident_count = self._reachable_entities(costs, budget, speed_kmh)
        areas = self._reachable_areas(geoms)

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
            "minutes": minutes,
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
            "reachable_entities": {
                "schools": schools,
                "school_count": len(schools),
                "accident_count": accident_count,
                "areas": areas,
            },
            "euclidean_vs_network": self._comparisons(lon, lat, schools),
            "notes": notes,
            "provenance": {
                "network_source": self.streets.source_name,
                "mode": self.streets.mode,
                "classification": "analytical result",
                "algorithm": "NetworkX single-source Dijkstra",
                "edge_weight": "length_m",
                "distance_crs": self.streets.provenance.get("metric_crs"),
                "walking_speed_kmh": speed_kmh,
                "time_budget_minutes": minutes,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "network": self.streets.provenance,
                "fallback_reason": self.streets.fallback_reason,
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

    def _reachable_entities(self, costs, budget, speed_kmh):
        schools, accidents = [], 0
        metres_per_minute = speed_kmh * 1000.0 / 60.0
        for entity_id, (access_node, connector) in self._entity_access.items():
            if access_node not in costs:
                continue
            distance = costs[access_node] + connector
            if distance > budget:
                continue
            entity = self.entity_graph.nodes[entity_id]
            if entity.get("type") == "Accident":
                accidents += 1
                continue
            schools.append({
                "id": entity_id,
                "name": entity.get("name"),
                "network_distance_m": round(distance, 1),
                "travel_time_minutes": round(distance / metres_per_minute, 1),
                "snap_distance_m": round(connector, 1),
                "geometry": entity.get("geometry"),
            })
        schools.sort(key=lambda row: row["network_distance_m"])
        return schools, accidents

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

    def _comparisons(self, lon, lat, schools):
        from .graph import haversine_m

        rows = []
        for school in schools:
            geometry = school.get("geometry") or {}
            if geometry.get("type") != "Point":
                continue
            slon, slat = geometry["coordinates"]
            euclidean = haversine_m((lon, lat), (slon, slat))
            rows.append({
                "id": school["id"],
                "name": school["name"],
                "euclidean_distance_m": round(euclidean, 1),
                "network_distance_m": school["network_distance_m"],
                "network_detour_factor": round(school["network_distance_m"] / euclidean, 2) if euclidean else 1.0,
            })
        return rows
