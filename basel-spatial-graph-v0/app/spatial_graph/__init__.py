"""Spatial Graph Core — a heterogeneous, typed, queryable graph of Basel.

This is a second representation of the city, deliberately separate from the
routing graphs the reference application uses. Those are optimized for shortest
paths; this one is optimized for typed relationships, multi-hop traversal,
cross-domain questions, aggregation, schema discovery and provenance.

    from app.spatial_graph import SpatialGraphService
    service = SpatialGraphService.load(engines={...})
    service.query({"start": {"type": "Neighborhood"}, "limit": 5})
"""
from __future__ import annotations

from typing import Dict, Optional

from ..config import SPATIAL_GRAPH_CACHE
from ..errors import SpatialGraphUnavailableError
from .analysis import AccessibilityAnalysis
from .builder import ORIGIN_METHOD, SpatialGraphBuilder, build_spatial_graph
from .model import MAX_RESULTS, NetworkXSpatialGraph, SpatialGraphStore, public_node
from .provenance import entity_provenance, query_provenance, relation_provenance
from .query import QueryEngine, QuerySpec
from .questions import QUESTIONS
from .schema import (
    NODE_TYPES,
    RELATION_TYPES,
    describe_schema,
    node_type,
    relation_type,
)

__all__ = [
    "AccessibilityAnalysis",
    "NetworkXSpatialGraph",
    "QUESTIONS",
    "QueryEngine",
    "QuerySpec",
    "SpatialGraphService",
    "SpatialGraphStore",
    "build_spatial_graph",
    "describe_schema",
    "public_node",
]


class SpatialGraphService:
    """What the API, the CLI and the tests all talk to."""

    def __init__(self, graph: NetworkXSpatialGraph,
                 engines: Optional[Dict] = None,
                 fallback_reason: Optional[str] = None):
        self.graph = graph
        self.fallback_reason = fallback_reason
        self.analysis = AccessibilityAnalysis(
            engines or {}, origin_method=graph.metadata.get("origin_method", ORIGIN_METHOD))
        self.engine = QueryEngine(graph, analysis_runner=self.analysis)

    # -- construction ---------------------------------------------------------
    @classmethod
    def load(cls, path=None, engines: Optional[Dict] = None,
             required: bool = False) -> Optional["SpatialGraphService"]:
        """Load the prepared graph, or return None if there isn't one."""
        try:
            graph = NetworkXSpatialGraph.load(path or SPATIAL_GRAPH_CACHE)
        except SpatialGraphUnavailableError:
            if required:
                raise
            return None
        return cls(graph, engines)

    @property
    def available(self) -> bool:
        return self.graph.graph.number_of_nodes() > 0

    # -- discovery ------------------------------------------------------------
    def schema(self) -> dict:
        described = describe_schema()
        counts = self.graph.node_counts()
        edges = self.graph.edge_counts()
        for name, entry in described["entity_types"].items():
            entry["count"] = counts.get(name, 0)
        for name, entry in described["relations"].items():
            entry["count"] = edges.get(name, 0)
        described["graph"] = self.graph.stats()
        described["analyses"]["accessibility"]["available_modes"] = self.analysis.available_modes
        return described

    def entity_types(self) -> dict:
        counts = self.graph.node_counts()
        return {
            "entity_types": [
                {**NODE_TYPES[name].describe(), "count": counts.get(name, 0)}
                for name in NODE_TYPES
            ],
            "total_nodes": self.graph.graph.number_of_nodes(),
        }

    def relation_types(self) -> dict:
        counts = self.graph.edge_counts()
        return {
            "relations": [
                {**RELATION_TYPES[name].describe(), "count": counts.get(name, 0)}
                for name in RELATION_TYPES
            ],
            "total_edges": self.graph.graph.number_of_edges(),
        }

    # -- retrieval ------------------------------------------------------------
    def entities(self, type_name: str, limit: int = 50, offset: int = 0,
                 include_geometry: bool = False, filters=None) -> dict:
        node_type(type_name)
        rows = list(self.graph.nodes_of_type(type_name))
        if filters:
            from .query import Filter

            parsed = [Filter.parse(f, type_name) for f in filters]
            rows = [row for row in rows if all(f.matches(row) for f in parsed)]
        total = len(rows)
        limit = min(int(limit), MAX_RESULTS)
        page = rows[offset:offset + limit]
        return {
            "type": type_name,
            "count": len(page),
            "total": total,
            "offset": offset,
            "limit": limit,
            "results": [public_node(row, include_geometry) for row in page],
        }

    def entity(self, type_name: str, entity_id: str, include_geometry: bool = False) -> dict:
        node_type(type_name)
        node = self.graph.get_node(entity_id)
        if node.get("type") != type_name:
            from ..errors import UnknownEntityError

            raise UnknownEntityError(
                f"'{entity_id}' is a {node.get('type')}, not a {type_name}.")
        return {**public_node(node, include_geometry),
                "provenance": entity_provenance(node)}

    def neighbors(self, type_name: str, entity_id: str, relation: Optional[str] = None,
                  target_type: Optional[str] = None, limit: int = 100,
                  include_geometry: bool = False) -> dict:
        self.entity(type_name, entity_id)
        if relation:
            relation_type(relation)
        rows = self.graph.neighbors(entity_id, relation=relation, target_type=target_type,
                                    limit=min(int(limit), MAX_RESULTS))
        by_relation: Dict[str, int] = {}
        for row in rows:
            by_relation[row["relation"]] = by_relation.get(row["relation"], 0) + 1
        return {
            "id": entity_id, "type": type_name, "count": len(rows),
            "by_relation": by_relation,
            "neighbors": [
                {"relation": row["relation"], "properties": row["properties"],
                 "node": public_node(row["node"], include_geometry)}
                for row in rows
            ],
        }

    def subgraph(self, type_name: str, entity_id: str, depth: int = 2,
                 relations=None, limit: int = 200, include_geometry: bool = False) -> dict:
        self.entity(type_name, entity_id)
        if relations:
            for name in relations:
                relation_type(name)
        result = self.graph.traverse(entity_id, depth=min(int(depth), 4), relations=relations,
                                     limit=min(int(limit), MAX_RESULTS))
        return {
            "root": entity_id, "depth": depth, "truncated": result["truncated"],
            "node_count": len(result["nodes"]), "edge_count": len(result["edges"]),
            "nodes": [public_node(node, include_geometry) for node in result["nodes"]],
            "edges": result["edges"],
        }

    # -- querying -------------------------------------------------------------
    def query(self, raw: dict) -> dict:
        spec = QuerySpec.parse(raw)
        before = self.analysis.stats()
        result = self.engine.run(spec)
        after = self.analysis.stats()
        result["provenance"] = query_provenance(self.graph, spec, {
            "engine_calls": after["engine_calls"] - before["engine_calls"],
            "cache_hits": after["cache_hits"] - before["cache_hits"],
            "modes": after["modes"],
        })
        return result

    def ask(self, name: str, **params) -> dict:
        """Run one of the standing questions."""
        from ..errors import QuerySpecError

        if name not in QUESTIONS:
            raise QuerySpecError(f"Unknown question '{name}'.", known=sorted(QUESTIONS))
        before = self.analysis.stats()
        answer = QUESTIONS[name](self.graph, self.analysis, **params)
        after = self.analysis.stats()
        answer["provenance"] = {
            "graph_generated_at": self.graph.metadata.get("generated_at"),
            "graph_mode": self.graph.metadata.get("mode"),
            "origin_method": self.graph.metadata.get("origin_method"),
            "population_reference_year": self.graph.metadata.get("population_reference_year"),
            "sources": self.graph.metadata.get("sources"),
            "analysis_engine": {
                "engine_calls": after["engine_calls"] - before["engine_calls"],
                "cache_hits": after["cache_hits"] - before["cache_hits"],
            },
            "result_kinds": {
                "persisted_graph_relations": ["ADJACENT_TO", "HAS_SERVICE", "HAS_TRANSIT_STOP",
                                              "HAS_POPULATION_OBSERVATION"],
                "dynamic_analytical_computation": ["accessibility counts", "nearest times"],
            },
        }
        return answer

    def provenance(self, entity_id: str) -> dict:
        if entity_id in RELATION_TYPES:
            return relation_provenance(entity_id)
        return entity_provenance(self.graph.get_node(entity_id))

    def status(self) -> dict:
        metadata = dict(self.graph.metadata)
        return {
            "available": self.available,
            "mode": metadata.get("mode"),
            "generated_at": metadata.get("generated_at"),
            "cache_path": metadata.get("cache_path"),
            "fallback_reason": self.fallback_reason,
            "nodes": self.graph.graph.number_of_nodes(),
            "edges": self.graph.graph.number_of_edges(),
            "node_types": self.graph.node_counts(),
            "relation_types": self.graph.edge_counts(),
            "population_reference_year": metadata.get("population_reference_year"),
            "population_years": metadata.get("population_years", []),
            "origin_method": metadata.get("origin_method"),
            "analysis_modes": self.analysis.available_modes,
            "warnings": metadata.get("warnings", []),
            "sources": metadata.get("sources", {}),
        }
