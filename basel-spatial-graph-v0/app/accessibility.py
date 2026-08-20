"""Deterministic, network-weighted walking accessibility."""
from __future__ import annotations

from datetime import datetime, timezone

import networkx as nx
from shapely.geometry import LineString, mapping, shape
from shapely.ops import unary_union

from .graph import haversine_m
from .street import StreetNetwork

DEFAULT_WALKING_SPEED_KMH = 4.8


class WalkingAccessibilityService:
    mode = "walk"

    def __init__(self, streets: StreetNetwork, entity_graph: nx.MultiDiGraph):
        self.streets = streets
        self.entity_graph = entity_graph
        self._entity_access = {}
        for node_id, data in entity_graph.nodes(data=True):
            if data.get("type") in {"School", "Accident"} and data.get("geometry", {}).get("type") == "Point":
                lon, lat = data["geometry"]["coordinates"]
                self._entity_access[node_id] = streets.nearest_node(lat, lon)[0]

    @staticmethod
    def budget_m(minutes: float, speed_kmh: float) -> float:
        return speed_kmh * 1000 * minutes / 60

    def calculate(self, lat: float, lon: float, minutes: float, speed_kmh: float = DEFAULT_WALKING_SPEED_KMH) -> dict:
        origin, snap_m = self.streets.nearest_node(lat, lon)
        budget = self.budget_m(minutes, speed_kmh)
        costs = nx.single_source_dijkstra_path_length(self.streets.graph, origin, cutoff=budget, weight="length_m")
        reachable = set(costs)
        edge_rows, lines = [], []
        for u, v, data in self.streets.graph.edges(data=True):
            if u in reachable and v in reachable and max(costs[u], costs[v]) <= budget + 1e-9:
                edge_rows.append((u, v, data))
                lines.append(shape(data["geometry"]))
        features = [{"type": "Feature", "geometry": d["geometry"], "properties": {"source": u, "target": v, "length_m": round(d["length_m"], 1)}} for u, v, d in edge_rows]
        if lines:
            # A narrow buffered network corridor avoids implying an exact parcel-level isochrone.
            boundary = unary_union(lines).buffer(.00022)
            features.append({"type": "Feature", "geometry": mapping(boundary), "properties": {"kind": "approximate_boundary", "method": "reachable-network buffer in EPSG:4326", "approximate": True}})
        schools = []
        for entity_id, access_node in self._entity_access.items():
            entity = self.entity_graph.nodes[entity_id]
            if entity.get("type") != "School" or access_node not in costs:
                continue
            entity_lon, entity_lat = entity["geometry"]["coordinates"]
            access = self.streets.graph.nodes[access_node]
            connector = haversine_m((entity_lon, entity_lat), (access["lon"], access["lat"]))
            distance = costs[access_node] + connector
            if distance <= budget:
                schools.append({"id": entity_id, "name": entity.get("name"), "network_distance_m": round(distance, 1), "travel_time_minutes": round(distance / (speed_kmh * 1000 / 60), 1), "geometry": entity["geometry"]})
        schools.sort(key=lambda row: row["network_distance_m"])
        reachable_lines = unary_union(lines) if lines else LineString()
        areas = []
        for entity_id, entity in self.entity_graph.nodes(data=True):
            if entity.get("type") == "Area" and not reachable_lines.is_empty and shape(entity["geometry"]).intersects(reachable_lines):
                areas.append({"id": entity_id, "name": entity.get("name"), "method": "polygon intersects reachable street segments"})
        comparisons = []
        for school in schools:
            slon, slat = school["geometry"]["coordinates"]
            euclidean = haversine_m((lon, lat), (slon, slat))
            comparisons.append({"id": school["id"], "name": school["name"], "euclidean_distance_m": round(euclidean, 1), "network_distance_m": school["network_distance_m"], "network_detour_factor": round(school["network_distance_m"] / euclidean, 2) if euclidean else 1.0})
        return {
            "origin": {"lat": lat, "lon": lon, "snap_distance_m": round(snap_m, 1)},
            "mode": "walk", "minutes": minutes, "walking_speed_kmh": speed_kmh,
            "network": {"origin_node_id": origin, "reachable_node_count": len(reachable), "reachable_edge_count": len(edge_rows), "max_network_distance_m": round(max(costs.values(), default=0), 1), "distance_budget_m": round(budget, 1), "total_reachable_edge_length_m": round(sum(d["length_m"] for _, _, d in edge_rows), 1)},
            "geometry": {"type": "FeatureCollection", "features": features},
            "reachable_entities": {"schools": schools, "areas": areas},
            "euclidean_vs_network": comparisons,
            "provenance": {"classification": "analytical result", "algorithm": "NetworkX single-source Dijkstra", "edge_weight": "length_m", "walking_speed_kmh": speed_kmh, "time_budget_minutes": minutes, "generated_at": datetime.now(timezone.utc).isoformat(), "network": self.streets.provenance},
        }
