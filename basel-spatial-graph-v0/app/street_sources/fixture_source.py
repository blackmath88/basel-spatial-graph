"""Deterministic synthetic walking grid.

Used by tests and as an explicit, clearly-labelled fallback. It is centred on
Basel for demo convenience but is NOT real Basel geography, and every response
built from it reports `mode: fixture`.
"""
from __future__ import annotations

from typing import Optional

import networkx as nx
from shapely.geometry import LineString

from .base import FIXTURE, StreetNetwork, WalkingNetworkSource, make_provenance

GRID_LONS = [7.574 + i * 0.006 for i in range(7)]
GRID_LATS = [47.550 + j * 0.004 for j in range(5)]


class FixtureWalkingNetworkSource(WalkingNetworkSource):
    name = "synthetic fixture"

    def __init__(self, reason: Optional[str] = None):
        self.reason = reason

    def load(self) -> StreetNetwork:
        graph = nx.Graph()
        for j, lat in enumerate(GRID_LATS):
            for i, lon in enumerate(GRID_LONS):
                graph.add_node(f"fixture:{i}:{j}", lon=lon, lat=lat, type="StreetNode")
        for j in range(len(GRID_LATS)):
            for i in range(len(GRID_LONS)):
                here = f"fixture:{i}:{j}"
                for ni, nj in ((i + 1, j), (i, j + 1)):
                    if ni >= len(GRID_LONS) or nj >= len(GRID_LATS):
                        continue
                    # A synthetic barrier at x=3, crossed only on rows 1 and 4:
                    # it keeps "near but not reachable" testable.
                    if ni == 3 and i == 2 and j not in {1, 4}:
                        continue
                    there = f"fixture:{ni}:{nj}"
                    a, b = graph.nodes[here], graph.nodes[there]
                    geom = LineString([(a["lon"], a["lat"]), (b["lon"], b["lat"])])
                    graph.add_edge(here, there, geom=geom, type="WALKABLE_TO", highway="fixture_path")
        provenance = make_provenance(
            mode=FIXTURE,
            source=self.name,
            dataset="Synthetic Basel-centred walking grid",
            license="fixture-only; not real observations",
        )
        return StreetNetwork(graph, provenance, self.reason)


def fixture_street_network(reason: Optional[str] = None) -> StreetNetwork:
    return FixtureWalkingNetworkSource(reason).load()
