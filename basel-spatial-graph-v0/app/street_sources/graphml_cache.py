"""Read/write the normalized walking-network cache as GraphML.

The cache is written once by `python -m app.prepare_data` and read on every
backend start. Reading deliberately depends only on networkx + shapely, so the
running API never needs OSMnx (or a network connection) installed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import networkx as nx
from shapely import wkt
from shapely.errors import ShapelyError

from ..errors import NetworkSourceError
from .base import StreetNetwork

# Graph-level attributes round-tripped through GraphML as the provenance record.
PROVENANCE_KEYS = (
    "mode", "source", "dataset", "source_url", "license", "retrieved_at",
    "crs", "metric_crs", "place", "network_type", "network", "osmnx_version", "attribution",
)


def write_cache(network: StreetNetwork, path: Path) -> Path:
    """Serialize a StreetNetwork to GraphML at `path`."""
    out = nx.Graph()
    for key in PROVENANCE_KEYS:
        value = network.provenance.get(key)
        if value is not None:
            out.graph[key] = str(value)
    for node, data in network.graph.nodes(data=True):
        out.add_node(
            str(node),
            lon=float(data["lon"]),
            lat=float(data["lat"]),
            osmid=str(data.get("osmid", node)),
        )
    for u, v, data in network.graph.edges(data=True):
        out.add_edge(
            str(u), str(v),
            length_m=float(data["length_m"]),
            geometry=data["geom"].wkt,
            highway=str(data.get("highway") or ""),
            name=str(data.get("name") or ""),
            osmid=str(data.get("osmid") or ""),
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(out, str(path))
    return path


def read_cache(path: Path, fallback_reason: Optional[str] = None) -> StreetNetwork:
    """Load a StreetNetwork from a GraphML cache written by `write_cache`."""
    if not Path(path).exists():
        raise NetworkSourceError(f"No cached walking network at {path}")
    try:
        raw = nx.read_graphml(str(path))
    except Exception as exc:  # corrupt or truncated cache
        raise NetworkSourceError(f"Cached walking network at {path} is unreadable: {exc}")

    graph = nx.Graph()
    for node, data in raw.nodes(data=True):
        try:
            graph.add_node(
                str(node),
                lon=float(data["lon"]),
                lat=float(data["lat"]),
                osmid=data.get("osmid", str(node)),
                type="StreetNode",
            )
        except (KeyError, TypeError, ValueError):
            continue
    for u, v, data in raw.edges(data=True):
        u, v = str(u), str(v)
        if u not in graph or v not in graph:
            continue
        geom = None
        if data.get("geometry"):
            try:
                geom = wkt.loads(data["geometry"])
            except (ShapelyError, TypeError, ValueError):
                geom = None
        graph.add_edge(
            u, v,
            length_m=data.get("length_m"),
            geom=geom,
            highway=data.get("highway") or None,
            name=data.get("name") or None,
            osmid=data.get("osmid") or None,
            type="WALKABLE_TO",
        )
    if graph.number_of_edges() == 0:
        raise NetworkSourceError(f"Cached walking network at {path} contains no edges")

    provenance = {key: raw.graph.get(key) for key in PROVENANCE_KEYS if raw.graph.get(key)}
    provenance.setdefault("mode", "live")
    provenance["fixture"] = provenance["mode"] == "fixture"
    provenance["cache_path"] = str(path)
    return StreetNetwork(graph, provenance, fallback_reason)
