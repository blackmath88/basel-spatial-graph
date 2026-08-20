"""City-wide analysis: invert the query and search for accessibility gaps.

Instead of asking "what can I reach from here?", ask "where in Basel can this
category NOT be reached within N minutes?". The trick is that on an undirected
walking graph the distance from a node to the nearest pharmacy equals the
distance from the set of all pharmacies to that node — so one multi-source
Dijkstra answers it for all 10,232 nodes at once.
"""
from __future__ import annotations

import heapq
from typing import Dict, List, Optional

import numpy as np
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from .errors import UnknownCategoryError
from .service_model import ServiceCategory, category_label, parse_category

# Uncovered sample points are thinned to this spacing so the result is a spread
# of representative places, not 25 nodes on one street corner.
GAP_SAMPLE_SPACING_M = 400.0

METHODOLOGY = (
    "Coverage is measured at every node of the pedestrian walking network. For each node the "
    "network distance to the nearest prepared service of the category is computed with a single "
    "multi-source Dijkstra seeded at each service's access node (offset by its snap distance). "
    "A node is 'covered' when that distance is within the time budget. Neighbourhood figures are "
    "the share of that neighbourhood's network nodes that are covered — this is a proxy for "
    "walkable street coverage, NOT a population-weighted accessibility measure: nobody lives on "
    "most of these nodes in equal numbers, and residential density is not taken into account."
)


def nearest_service_distances(streets, service_index, category: ServiceCategory,
                              cutoff: Optional[float] = None) -> Dict[str, float]:
    """Network distance from every node to the nearest service of `category`."""
    graph = streets.graph
    distances: Dict[str, float] = {}
    heap = []
    for service in service_index.by_category.get(category, ()):
        if not service.is_routable or service.access_node_id not in graph:
            continue
        start = float(service.access_distance_m or 0.0)
        if distances.get(service.access_node_id, float("inf")) > start:
            distances[service.access_node_id] = start
            heapq.heappush(heap, (start, service.access_node_id))
    if not heap:
        return {}
    while heap:
        distance, node = heapq.heappop(heap)
        if distance > distances.get(node, float("inf")):
            continue
        if cutoff is not None and distance > cutoff:
            continue
        for neighbour, data in graph[node].items():
            candidate = distance + data["length_m"]
            if candidate < distances.get(neighbour, float("inf")):
                distances[neighbour] = candidate
                heapq.heappush(heap, (candidate, neighbour))
    return distances


class CityAnalysis:
    """Holds the node -> neighbourhood assignment so gap queries stay cheap."""

    def __init__(self, streets, service_index, entity_graph=None):
        self.streets = streets
        self.services = service_index
        self.entity_graph = entity_graph
        self._areas = None
        self._node_area = None

    # -- lazy indexes ---------------------------------------------------------
    def _ensure_areas(self):
        if self._node_area is not None:
            return
        areas, shapes = [], []
        if self.entity_graph is not None:
            for node_id, data in self.entity_graph.nodes(data=True):
                if data.get("type") != "Area" or not isinstance(data.get("geometry"), dict):
                    continue
                try:
                    geometry = shape(data["geometry"])
                except Exception:
                    continue
                areas.append({"id": node_id, "name": data.get("name"), "geometry": geometry})
                shapes.append(geometry)
        self._areas = areas
        self._node_area = {}
        if not shapes:
            return
        node_ids = list(self.streets.graph.nodes)
        points = [Point(self.streets.graph.nodes[n]["lon"], self.streets.graph.nodes[n]["lat"])
                  for n in node_ids]
        tree = STRtree(shapes)
        try:
            hits = tree.query(np.array(points, dtype=object), predicate="within")
            for point_index, area_index in zip(hits[0], hits[1]):
                self._node_area.setdefault(node_ids[int(point_index)], int(area_index))
        except Exception:
            # Older shapely: fall back to a per-point query.
            for node_id, point in zip(node_ids, points):
                for area_index in tree.query(point):
                    index = int(area_index)
                    if shapes[index].contains(point):
                        self._node_area[node_id] = index
                        break

    # -- the query ------------------------------------------------------------
    def accessibility_gaps(self, category, minutes: float, speed_kmh: float,
                           limit: int = 25) -> dict:
        category = parse_category(category)
        prepared = self.services.by_category.get(category)
        if not prepared:
            raise UnknownCategoryError(
                f"No prepared services for category '{category.value}'.",
                known=[c.value for c in self.services.categories],
            )
        budget = speed_kmh * 1000.0 * minutes / 60.0
        distances = nearest_service_distances(self.streets, self.services, category)
        graph = self.streets.graph
        nodes = list(graph.nodes)

        uncovered = [(n, distances.get(n, float("inf"))) for n in nodes
                     if distances.get(n, float("inf")) > budget]
        covered_count = len(nodes) - len(uncovered)

        self._ensure_areas()
        per_area = self._area_coverage(nodes, distances, budget)
        samples = self._sample_points(uncovered, limit)

        return {
            "category": category.value,
            "label": category_label(category),
            "minutes": minutes,
            "walking_speed_kmh": speed_kmh,
            "distance_budget_m": round(budget, 1),
            "prepared_service_count": len(prepared),
            "routable_service_count": sum(1 for s in prepared if s.is_routable),
            "network": {
                "node_count": len(nodes),
                "covered_node_count": covered_count,
                "uncovered_node_count": len(uncovered),
                "covered_ratio": round(covered_count / len(nodes), 4) if nodes else None,
            },
            "neighbourhoods": per_area,
            "worst_uncovered_points": {
                "type": "FeatureCollection",
                "features": samples,
            },
            "method": METHODOLOGY,
            "provenance": {
                "algorithm": "multi-source Dijkstra over the pedestrian network",
                "distance_crs": self.streets.provenance.get("metric_crs"),
                "network_source": self.streets.source_name,
                "network_mode": self.streets.mode,
                "services_mode": self.services.mode,
                "classification": "exploratory analytical result",
            },
        }

    def _area_coverage(self, nodes, distances, budget):
        if not self._areas:
            return []
        buckets: Dict[int, List[float]] = {}
        for node in nodes:
            index = self._node_area.get(node)
            if index is None:
                continue
            buckets.setdefault(index, []).append(distances.get(node, float("inf")))
        rows = []
        for index, values in buckets.items():
            area = self._areas[index]
            finite = [v for v in values if v != float("inf")]
            covered = sum(1 for v in values if v <= budget)
            rows.append({
                "id": area["id"],
                "name": area["name"],
                "node_count": len(values),
                "covered_node_count": covered,
                "covered_ratio": round(covered / len(values), 4) if values else None,
                "median_distance_m": round(float(np.median(finite)), 1) if finite else None,
                "max_distance_m": (round(max(finite), 1) if finite else None)
                                  if len(finite) == len(values) else None,
                "unreachable_node_count": len(values) - len(finite),
            })
        rows.sort(key=lambda row: (row["covered_ratio"] if row["covered_ratio"] is not None else 0))
        return rows

    def _sample_points(self, uncovered, limit):
        """Worst-first, thinned so the samples are spread across the city."""
        if not uncovered:
            return []
        by_node = dict(uncovered)
        ranked = sorted(uncovered, key=lambda pair: -(pair[1] if pair[1] != float("inf") else 1e12))
        graph = self.streets.graph
        chosen = []
        for node, _ in ranked:
            x, y = graph.nodes[node]["x"], graph.nodes[node]["y"]
            if any(np.hypot(x - cx, y - cy) < GAP_SAMPLE_SPACING_M for _, cx, cy in chosen):
                continue
            chosen.append((node, x, y))
            if len(chosen) >= limit:
                break
        features = []
        for node, _, _ in chosen:
            distance = by_node.get(node, float("inf"))
            area_index = self._node_area.get(node) if self._node_area else None
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [
                    round(graph.nodes[node]["lon"], 6), round(graph.nodes[node]["lat"], 6)]},
                "properties": {
                    "node_id": node,
                    "distance_to_nearest_m": None if distance == float("inf") else round(distance, 1),
                    "unreachable": distance == float("inf"),
                    "neighbourhood": self._areas[area_index]["name"] if area_index is not None else None,
                },
            })
        return features
