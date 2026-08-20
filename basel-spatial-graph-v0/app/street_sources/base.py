"""The street-network model and the contract every street source implements.

Nothing downstream of this module (accessibility, graph, API, UI) knows whether
a network came from OpenStreetMap or from the synthetic fixture, nor whether it
is the pedestrian or the bicycle graph. It only sees a `StreetNetwork`: an
undirected graph whose nodes carry `lon/lat` plus projected metric `x/y`, and
whose edges carry `length_m` and a WGS84 `geom` LineString.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

import networkx as nx
import numpy as np
from shapely.geometry import LineString, mapping

from ..config import GEOGRAPHIC_CRS, MAX_SNAP_DISTANCE_M, METRIC_CRS
from ..errors import EmptyNetworkError, InvalidCoordinateError, OutsideNetworkError
from ..projection import to_metric, validate_lonlat

LIVE = "live"
FIXTURE = "fixture"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_provenance(
    *,
    mode: str,
    source: str,
    dataset: str,
    source_url: Optional[str] = None,
    license: Optional[str] = None,
    retrieved_at: Optional[str] = None,
    **extra,
) -> dict:
    """One provenance shape for every source, so the UI can trust the fields."""
    return {
        "mode": mode,
        "fixture": mode == FIXTURE,
        "source": source,
        "dataset": dataset,
        "source_url": source_url,
        "license": license,
        "retrieved_at": retrieved_at,
        "crs": GEOGRAPHIC_CRS,
        "metric_crs": METRIC_CRS,
        **extra,
    }


class StreetNetwork:
    """An undirected, metre-weighted street network with a spatial index."""

    def __init__(self, graph: nx.Graph, provenance: dict, fallback_reason: Optional[str] = None):
        self.graph = graph
        self.provenance = dict(provenance)
        self.fallback_reason = fallback_reason
        self._node_ids: list = []
        self._xy = np.empty((0, 2), dtype=float)
        self.dropped_edges = 0
        self._normalize()
        self._build_index()

    # -- construction ---------------------------------------------------------
    def _normalize(self) -> None:
        """Fill projected coordinates, repair lengths, drop unusable edges."""
        nodes = list(self.graph.nodes)
        missing = [n for n in nodes if "x" not in self.graph.nodes[n] or "y" not in self.graph.nodes[n]]
        if missing:
            lons = [float(self.graph.nodes[n]["lon"]) for n in missing]
            lats = [float(self.graph.nodes[n]["lat"]) for n in missing]
            xs, ys = to_metric(lons, lats)
            for node, x, y in zip(missing, np.atleast_1d(xs), np.atleast_1d(ys)):
                self.graph.nodes[node]["x"] = float(x)
                self.graph.nodes[node]["y"] = float(y)

        unusable = []
        for u, v, data in self.graph.edges(data=True):
            geom = data.get("geom")
            if geom is None:
                geom = LineString([
                    (self.graph.nodes[u]["lon"], self.graph.nodes[u]["lat"]),
                    (self.graph.nodes[v]["lon"], self.graph.nodes[v]["lat"]),
                ])
                data["geom"] = geom
            length = data.get("length_m")
            try:
                length = float(length)
            except (TypeError, ValueError):
                length = float("nan")
            if not (length > 0) or length != length:
                # Missing or nonsensical length: recover it from projected node positions.
                length = float(np.hypot(
                    self.graph.nodes[u]["x"] - self.graph.nodes[v]["x"],
                    self.graph.nodes[u]["y"] - self.graph.nodes[v]["y"],
                ))
            if not (length > 0):
                unusable.append((u, v))
                continue
            data["length_m"] = length
        for u, v in unusable:
            self.graph.remove_edge(u, v)
        self.dropped_edges = len(unusable)

    def _build_index(self) -> None:
        self._node_ids = list(self.graph.nodes)
        if self._node_ids:
            self._xy = np.array(
                [(self.graph.nodes[n]["x"], self.graph.nodes[n]["y"]) for n in self._node_ids],
                dtype=float,
            )
        else:
            self._xy = np.empty((0, 2), dtype=float)

    # -- properties -----------------------------------------------------------
    @property
    def mode(self) -> str:
        return self.provenance.get("mode", FIXTURE)

    @property
    def is_live(self) -> bool:
        return self.mode == LIVE

    @property
    def source_name(self) -> str:
        return self.provenance.get("source", "unknown")

    @property
    def kind(self) -> str:
        """Which prepared network this is: `walk` or `bike`."""
        return self.provenance.get("network", "walk")

    def total_length_m(self) -> float:
        return float(sum(d["length_m"] for _, _, d in self.graph.edges(data=True)))

    def stats(self) -> dict:
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            "total_length_m": round(self.total_length_m(), 1),
            "mode": self.mode,
            "source": self.source_name,
            "network": self.kind,
            "crs": self.provenance.get("crs"),
            "metric_crs": self.provenance.get("metric_crs"),
            "dropped_edges": self.dropped_edges,
        }

    # -- queries --------------------------------------------------------------
    def nearest_node(self, lat: float, lon: float, max_distance_m: Optional[float] = MAX_SNAP_DISTANCE_M):
        """Snap a WGS84 click to the nearest usable node of this network.

        Returns (node_id, distance_m). Distance is measured in METRIC_CRS metres.
        """
        if not self._node_ids:
            raise EmptyNetworkError(f"The {self.kind} network contains no nodes.")
        try:
            lon_f, lat_f = validate_lonlat(lon, lat)
        except ValueError as exc:
            raise InvalidCoordinateError(str(exc), lat=lat, lon=lon)
        x, y = to_metric(lon_f, lat_f)
        distances = np.hypot(self._xy[:, 0] - float(x), self._xy[:, 1] - float(y))
        index = int(np.argmin(distances))
        distance = float(distances[index])
        if max_distance_m is not None and distance > max_distance_m:
            reachable_by = "walkable" if self.kind == "walk" else "cyclable"
            raise OutsideNetworkError(
                f"No {reachable_by} street within {max_distance_m:.0f} m of this location "
                f"(nearest node is {distance:.0f} m away). Click inside the covered area.",
                snap_distance_m=round(distance, 1),
                max_snap_distance_m=max_distance_m,
            )
        return self._node_ids[index], distance

    def nearest_nodes(self, points, chunk_size: int = 256):
        """Batch snapping for (lon, lat) pairs; returns [(node_id, distance_m)].

        Chunked so that snapping a few thousand services against a city-sized
        network never allocates a multi-hundred-megabyte distance matrix.
        """
        if not self._node_ids or not points:
            return []
        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        xs, ys = to_metric(lons, lats)
        xs = np.atleast_1d(np.asarray(xs, dtype=float))
        ys = np.atleast_1d(np.asarray(ys, dtype=float))
        results = []
        for start in range(0, len(xs), chunk_size):
            stop = start + chunk_size
            dx = self._xy[:, 0][None, :] - xs[start:stop, None]
            dy = self._xy[:, 1][None, :] - ys[start:stop, None]
            distances = np.hypot(dx, dy)
            indices = np.argmin(distances, axis=1)
            results.extend(
                (self._node_ids[int(i)], float(distances[row, int(i)]))
                for row, i in enumerate(indices)
            )
        return results

    def edge_feature(self, u, v, data: dict) -> dict:
        """GeoJSON for one edge, with its rounded geometry memoized.

        A city-wide query touches thousands of edges and the same edges come
        back on every query, so converting and rounding each one once is worth
        the dictionary slot.
        """
        geometry = data.get("geojson")
        if geometry is None:
            geometry = _round_coordinates(mapping(data["geom"]))
            data["geojson"] = geometry
        return {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "source": u,
                "target": v,
                "length_m": round(data["length_m"], 1),
                "highway": data.get("highway"),
                "name": data.get("name"),
            },
        }


def _round_coordinates(geometry: dict, precision: int = 6) -> dict:
    """Trim GeoJSON coordinate noise; halves the payload, changes nothing visible."""
    def walk(value):
        if isinstance(value, (list, tuple)):
            if value and isinstance(value[0], (int, float)):
                return [round(float(c), precision) for c in value]
            return [walk(item) for item in value]
        return value

    return {**geometry, "coordinates": walk(geometry["coordinates"])}


class WalkingNetworkSource(ABC):
    """Contract for anything that can supply a `StreetNetwork`."""

    name: str = "unknown"

    @abstractmethod
    def load(self) -> StreetNetwork:
        """Return a ready-to-use network or raise `NetworkSourceError`."""
