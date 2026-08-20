"""Graph storage and the small interface a different backend would implement.

The store is a NetworkX `MultiDiGraph` behind a deliberately narrow interface —
`get_node`, `neighbors`, `traverse`, `find`. Everything above this module talks
to that interface, so swapping in DuckDB, Neo4j or anything else later means
implementing four methods, not rewriting the query layer.

Persistence is plain JSON. Not pickle (fragile across versions, unsafe to load),
not GraphML (cannot hold the nested provenance and list attributes these nodes
carry). JSON is 2 MB here, loads in a fraction of a second, and can be read by
anything.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

import networkx as nx

from ..errors import SpatialGraphUnavailableError, UnknownEntityError

FORMAT_VERSION = 1
# Nothing may return more than this in one call, whatever the caller asks for.
MAX_RESULTS = 1000


class SpatialGraphStore:
    """The interface every backend must provide."""

    def get_node(self, node_id: str) -> dict:
        raise NotImplementedError

    def neighbors(self, node_id: str, relation: Optional[str] = None,
                  target_type: Optional[str] = None) -> List[dict]:
        raise NotImplementedError

    def traverse(self, node_id: str, depth: int = 1,
                 relations: Optional[Sequence[str]] = None) -> dict:
        raise NotImplementedError

    def find(self, node_type: str, limit: int = 100) -> List[dict]:
        raise NotImplementedError


class NetworkXSpatialGraph(SpatialGraphStore):
    """A heterogeneous, typed, directed multigraph of Basel."""

    def __init__(self, graph: Optional[nx.MultiDiGraph] = None,
                 metadata: Optional[dict] = None):
        self.graph = graph if graph is not None else nx.MultiDiGraph()
        self.metadata = dict(metadata or {})
        self._by_type: Dict[str, List[str]] = {}
        self.reindex()

    # -- construction ---------------------------------------------------------
    def reindex(self) -> None:
        self._by_type = {}
        for node_id, data in self.graph.nodes(data=True):
            self._by_type.setdefault(data.get("type", "Unknown"), []).append(node_id)

    def add_node(self, entity_id: str, node_type: str, **properties) -> str:
        """`entity_id`, not `node_id`: nodes here may carry a routing `node_id` property."""
        if entity_id not in self.graph:
            self._by_type.setdefault(node_type, []).append(entity_id)
        self.graph.add_node(entity_id, type=node_type, id=entity_id, **properties)
        return entity_id

    def add_edge(self, source: str, target: str, relation: str, **properties) -> None:
        self.graph.add_edge(source, target, key=relation, relation=relation, **properties)

    # -- the interface --------------------------------------------------------
    def get_node(self, node_id: str) -> dict:
        if node_id not in self.graph:
            raise UnknownEntityError(f"No entity '{node_id}' in the spatial graph.")
        return dict(self.graph.nodes[node_id])

    def neighbors(self, node_id: str, relation: Optional[str] = None,
                  target_type: Optional[str] = None, limit: int = MAX_RESULTS) -> List[dict]:
        """Outgoing edges, optionally narrowed by relation and target type."""
        if node_id not in self.graph:
            raise UnknownEntityError(f"No entity '{node_id}' in the spatial graph.")
        rows = []
        for _, target, key, data in self.graph.out_edges(node_id, keys=True, data=True):
            if relation and key != relation:
                continue
            node = self.graph.nodes[target]
            if target_type and node.get("type") != target_type:
                continue
            rows.append({"relation": key, "node": dict(node),
                         "properties": {k: v for k, v in data.items() if k != "relation"}})
            if len(rows) >= limit:
                break
        return rows

    def traverse(self, node_id: str, depth: int = 1,
                 relations: Optional[Sequence[str]] = None,
                 limit: int = MAX_RESULTS) -> dict:
        """Breadth-first neighbourhood of a node, bounded by depth and size."""
        if node_id not in self.graph:
            raise UnknownEntityError(f"No entity '{node_id}' in the spatial graph.")
        wanted = set(relations) if relations else None
        seen = {node_id}
        frontier = [node_id]
        edges = []
        for _ in range(max(1, depth)):
            next_frontier = []
            for current in frontier:
                for _, target, key, data in self.graph.out_edges(current, keys=True, data=True):
                    if wanted and key not in wanted:
                        continue
                    edges.append({"source": current, "target": target, "relation": key})
                    if target not in seen:
                        seen.add(target)
                        next_frontier.append(target)
                    if len(seen) >= limit:
                        break
                if len(seen) >= limit:
                    break
            frontier = next_frontier
            if not frontier or len(seen) >= limit:
                break
        return {
            "nodes": [dict(self.graph.nodes[n]) for n in seen],
            "edges": edges,
            "truncated": len(seen) >= limit,
        }

    def find(self, node_type: str, limit: int = MAX_RESULTS) -> List[dict]:
        return [dict(self.graph.nodes[n]) for n in self._by_type.get(node_type, [])[:limit]]

    # -- convenience ----------------------------------------------------------
    def nodes_of_type(self, node_type: str) -> Iterator[dict]:
        for node_id in self._by_type.get(node_type, []):
            yield dict(self.graph.nodes[node_id])

    def count_of_type(self, node_type: str) -> int:
        return len(self._by_type.get(node_type, []))

    def node_counts(self) -> Dict[str, int]:
        return {name: len(ids) for name, ids in sorted(self._by_type.items())}

    def edge_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for _, _, key in self.graph.edges(keys=True):
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def stats(self) -> dict:
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            "node_types": self.node_counts(),
            "relation_types": self.edge_counts(),
            **{k: v for k, v in self.metadata.items() if k != "sources"},
        }

    # -- persistence ----------------------------------------------------------
    def to_payload(self) -> dict:
        return {
            "format_version": FORMAT_VERSION,
            "metadata": self.metadata,
            "nodes": [dict(data) for _, data in self.graph.nodes(data=True)],
            "edges": [
                {"source": u, "target": v, "relation": key,
                 **{k: val for k, val in data.items() if k != "relation"}}
                for u, v, key, data in self.graph.edges(keys=True, data=True)
            ],
        }

    def save(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_payload(), ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path) -> "NetworkXSpatialGraph":
        path = Path(path)
        if not path.exists():
            raise SpatialGraphUnavailableError(
                f"No prepared spatial graph at {path}. "
                "Run `python -m app.prepare_spatial_graph`."
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SpatialGraphUnavailableError(f"The spatial graph at {path} is unreadable: {exc}")
        if payload.get("format_version") != FORMAT_VERSION:
            raise SpatialGraphUnavailableError(
                f"The spatial graph at {path} was written by another version "
                f"({payload.get('format_version')} != {FORMAT_VERSION}). Re-run "
                "`python -m app.prepare_spatial_graph`."
            )
        graph = nx.MultiDiGraph()
        for node in payload.get("nodes", []):
            graph.add_node(node["id"], **node)
        for edge in payload.get("edges", []):
            graph.add_edge(edge["source"], edge["target"], key=edge["relation"],
                           **{k: v for k, v in edge.items()
                              if k not in {"source", "target"}})
        metadata = dict(payload.get("metadata", {}))
        metadata["cache_path"] = str(path)
        return cls(graph, metadata)


def public_node(node: dict, include_geometry: bool = False,
                fields: Optional[Sequence[str]] = None) -> dict:
    """A node as an API returns it: no geometry unless asked, no internals."""
    row = {k: v for k, v in node.items() if not k.startswith("_")}
    if not include_geometry:
        row.pop("geometry", None)
    if fields:
        keep = set(fields) | {"id", "type"}
        row = {k: v for k, v in row.items() if k in keep}
    return row
