"""Walking-network source adapters and a deterministic Basel-centred fallback."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
import networkx as nx

from .config import BASEL_BBOX, OVERPASS_URL, STREET_CACHE
from .graph import haversine_m

OSM_COPYRIGHT = "OpenStreetMap contributors"


def _provenance(mode: str, retrieved_at: Optional[str] = None) -> dict:
    fixture = mode == "fixture"
    return {
        "source": "synthetic fixture" if fixture else OSM_COPYRIGHT,
        "dataset": "Basel pedestrian walking network",
        "source_url": None if fixture else "https://www.openstreetmap.org/copyright",
        "retrieved_at": retrieved_at,
        "license": "fixture-only; not real observations" if fixture else "ODbL 1.0",
        "crs": "EPSG:4326",
        "mode": mode,
        "fixture": fixture,
    }


class StreetNetwork:
    def __init__(self, graph: nx.Graph, provenance: dict, fallback_reason: Optional[str] = None):
        self.graph = graph
        self.provenance = provenance
        self.fallback_reason = fallback_reason

    def nearest_node(self, lat: float, lon: float) -> tuple[str, float]:
        if not self.graph:
            raise RuntimeError("Walking network is empty")
        node_id, distance = min(
            ((str(n), haversine_m((lon, lat), (d["lon"], d["lat"]))) for n, d in self.graph.nodes(data=True)),
            key=lambda item: item[1],
        )
        return node_id, distance


def _graph_from_payload(payload: dict) -> StreetNetwork:
    graph = nx.Graph()
    for node in payload["nodes"]:
        graph.add_node(str(node["id"]), lon=float(node["lon"]), lat=float(node["lat"]), type="StreetNode")
    for edge in payload["edges"]:
        u, v = str(edge["u"]), str(edge["v"])
        if u not in graph or v not in graph:
            continue
        length = float(edge.get("length_m") or haversine_m(
            (graph.nodes[u]["lon"], graph.nodes[u]["lat"]),
            (graph.nodes[v]["lon"], graph.nodes[v]["lat"]),
        ))
        geometry = edge.get("geometry") or {
            "type": "LineString",
            "coordinates": [[graph.nodes[u]["lon"], graph.nodes[u]["lat"]], [graph.nodes[v]["lon"], graph.nodes[v]["lat"]]],
        }
        # Keep the shortest parallel OSM segment; routing currently needs an undirected walk graph.
        if not graph.has_edge(u, v) or length < graph[u][v]["length_m"]:
            graph.add_edge(u, v, length_m=length, geometry=geometry, type="WALKABLE_TO")
    return StreetNetwork(graph, payload["provenance"])


def _fetch_osm() -> StreetNetwork:
    south, west, north, east = BASEL_BBOX
    query = f"""[out:json][timeout:45];way[highway][highway!~\"motorway|motorway_link|trunk|trunk_link|construction|proposed\"][access!~\"private|no\"][foot!~\"private|no\"]({south},{west},{north},{east});(._;>;);out body;"""
    response = httpx.post(OVERPASS_URL, data={"data": query}, timeout=60, follow_redirects=True)
    response.raise_for_status()
    elements = response.json().get("elements", [])
    coords = {str(e["id"]): (e["lon"], e["lat"]) for e in elements if e.get("type") == "node" and "lon" in e}
    nodes, edges = {}, []
    for way in (e for e in elements if e.get("type") == "way"):
        ids = [str(n) for n in way.get("nodes", []) if str(n) in coords]
        for u, v in zip(ids, ids[1:]):
            nodes[u] = coords[u]; nodes[v] = coords[v]
            line = [list(coords[u]), list(coords[v])]
            edges.append({"u": u, "v": v, "length_m": haversine_m(coords[u], coords[v]), "geometry": {"type": "LineString", "coordinates": line}})
    if not edges:
        raise RuntimeError("Overpass returned no usable walking edges")
    retrieved = datetime.now(timezone.utc).isoformat()
    payload = {
        "nodes": [{"id": n, "lon": xy[0], "lat": xy[1]} for n, xy in nodes.items()],
        "edges": edges,
        "provenance": _provenance("live", retrieved),
    }
    STREET_CACHE.parent.mkdir(parents=True, exist_ok=True)
    STREET_CACHE.write_text(json.dumps(payload), encoding="utf-8")
    return _graph_from_payload(payload)


def fixture_street_network(reason: Optional[str] = None) -> StreetNetwork:
    """Small synthetic grid with gaps: stable for tests, never presented as Basel truth."""
    graph = nx.Graph()
    lons = [7.574 + i * .006 for i in range(7)]
    lats = [47.550 + j * .004 for j in range(5)]
    for j, lat in enumerate(lats):
        for i, lon in enumerate(lons):
            graph.add_node(f"fixture:{i}:{j}", lon=lon, lat=lat, type="StreetNode")
    for j in range(len(lats)):
        for i in range(len(lons)):
            here = f"fixture:{i}:{j}"
            for ni, nj in ((i + 1, j), (i, j + 1)):
                if ni >= len(lons) or nj >= len(lats):
                    continue
                # A synthetic barrier around x=3, crossed only on rows 1 and 4.
                if ni == 3 and i == 2 and j not in {1, 4}:
                    continue
                there = f"fixture:{ni}:{nj}"
                a, b = graph.nodes[here], graph.nodes[there]
                geometry = {"type": "LineString", "coordinates": [[a["lon"], a["lat"]], [b["lon"], b["lat"]]]}
                graph.add_edge(here, there, length_m=haversine_m(geometry["coordinates"][0], geometry["coordinates"][1]), geometry=geometry, type="WALKABLE_TO")
    return StreetNetwork(graph, _provenance("fixture"), reason)


def load_street_network(force_fixture: bool = False) -> StreetNetwork:
    if force_fixture or os.getenv("BASEL_STREET_NETWORK_SOURCE", "auto") == "fixture":
        return fixture_street_network("Fixture mode requested")
    try:
        if STREET_CACHE.exists():
            return _graph_from_payload(json.loads(STREET_CACHE.read_text(encoding="utf-8")))
        return _fetch_osm()
    except Exception as exc:
        return fixture_street_network(str(exc))
