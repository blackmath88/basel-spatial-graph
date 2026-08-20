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


NODE_PREFIX = {"walk": "fixture", "bike": "bike"}
EDGE_TYPE = {"walk": "WALKABLE_TO", "bike": "CYCLABLE_TO"}


class FixtureWalkingNetworkSource(WalkingNetworkSource):
    name = "synthetic fixture"

    def __init__(self, reason: Optional[str] = None, kind: str = "walk"):
        self.reason = reason
        self.kind = kind

    def load(self) -> StreetNetwork:
        prefix = NODE_PREFIX.get(self.kind, self.kind)
        edge_type = EDGE_TYPE.get(self.kind, "WALKABLE_TO")
        graph = nx.Graph()
        for j, lat in enumerate(GRID_LATS):
            for i, lon in enumerate(GRID_LONS):
                graph.add_node(f"{prefix}:{i}:{j}", lon=lon, lat=lat, type="StreetNode")
        for j in range(len(GRID_LATS)):
            for i in range(len(GRID_LONS)):
                here = f"{prefix}:{i}:{j}"
                for ni, nj in ((i + 1, j), (i, j + 1)):
                    if ni >= len(GRID_LONS) or nj >= len(GRID_LATS):
                        continue
                    # The walking barrier at x=3, crossed only on rows 1 and 4.
                    # Bicycles have the crossing the footpath network lacks.
                    if self.kind == "walk" and ni == 3 and i == 2 and j not in {1, 4}:
                        continue
                    there = f"{prefix}:{ni}:{nj}"
                    a, b = graph.nodes[here], graph.nodes[there]
                    geom = LineString([(a["lon"], a["lat"]), (b["lon"], b["lat"])])
                    graph.add_edge(here, there, geom=geom, type=edge_type,
                                   highway=f"fixture_{self.kind}_path")
        provenance = make_provenance(
            mode=FIXTURE,
            source=self.name,
            dataset=f"Synthetic Basel-centred {self.kind} grid",
            license="fixture-only; not real observations",
            network=self.kind,
        )
        return StreetNetwork(graph, provenance, self.reason)


class FixtureCyclingNetworkSource(FixtureWalkingNetworkSource):
    def __init__(self, reason: Optional[str] = None):
        super().__init__(reason, kind="bike")


def fixture_street_network(reason: Optional[str] = None, kind: str = "walk") -> StreetNetwork:
    return FixtureWalkingNetworkSource(reason, kind).load()
