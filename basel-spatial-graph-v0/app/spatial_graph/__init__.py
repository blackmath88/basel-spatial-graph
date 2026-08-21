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
from ..snapshot import runtime_snapshot
from .analysis import AccessibilityAnalysis
from .builder import ORIGIN_METHOD, build_spatial_graph
from .model import MAX_RESULTS, NetworkXSpatialGraph, SpatialGraphStore, public_node
from .provenance import (entity_provenance, query_provenance, relation_provenance,
                         shared_provenance)
from ..data_quality import relevant_caveats
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
        provenance = entity_provenance(node)
        provenance["data_mode"] = provenance.get("data_mode") or self.graph.metadata.get("mode")
        return {**public_node(node, include_geometry), "provenance": provenance}

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
        computations = result["execution"].pop("computation_provenance", [])
        result["provenance"] = query_provenance(self.graph, spec, {
            "engine_calls": after["engine_calls"] - before["engine_calls"],
            "cache_hits": after["cache_hits"] - before["cache_hits"],
            "modes": after["modes"],
            "computations": computations,
        })
        return result

    def ask(self, name: str, **params) -> dict:
        """Run one of the standing questions."""
        from ..errors import QuerySpecError

        if name not in QUESTIONS:
            raise QuerySpecError(f"Unknown question '{name}'.", known=sorted(QUESTIONS))
        before = self.analysis.stats()
        self.analysis.begin_trace()
        answer = QUESTIONS[name](self.graph, self.analysis, **params)
        after = self.analysis.stats()
        descriptor = answer.pop("_provenance", {})
        quality_spec = descriptor.get("quality") or {}
        quality = relevant_caveats(
            self.graph.metadata.get("data_quality"),
            categories=quality_spec.get("categories", ()),
            networks=quality_spec.get("networks", ()),
            transit=quality_spec.get("transit", False))
        if descriptor.get("fields"):
            for caveat in quality.get("caveats", []):
                code = caveat["code"]
                if code.startswith("service_") or code.startswith("services_"):
                    caveat["applies_to"] = ["results[].pharmacy_count",
                                             "results[].pharmacy_nearest_minutes"]
                elif code.startswith("transit_"):
                    caveat["applies_to"] = ["results[].transit_stops_in_walking_range"]
                elif code.startswith("network_"):
                    caveat["applies_to"] = ["results[].pharmacy_count",
                                             "results[].pharmacy_nearest_minutes"]
                    if quality_spec.get("transit"):
                        caveat["applies_to"].append(
                            "results[].transit_stops_in_walking_range")
        relations = [relation_provenance(item) for item in descriptor.get("relations", [])]
        computations = descriptor.get("computations") or self.analysis.traced_computations()
        answer["provenance"] = shared_provenance(
            self.graph, types=descriptor.get("types", ["Neighborhood"]),
            relations=relations,
            analyses=[{"classification": "dynamic", "computed_by":
                       "app.accessibility / app.multimodal"}],
            computations=computations, fields=descriptor.get("fields"), quality=quality,
            analysis_stats={
                "engine_calls": after["engine_calls"] - before["engine_calls"],
                "cache_hits": after["cache_hits"] - before["cache_hits"],
            })
        # Compatibility key retained while the field registry is authoritative.
        answer["provenance"]["result_kinds"] = {
            "persisted_graph_relations": ["ADJACENT_TO", "HAS_SERVICE", "HAS_TRANSIT_STOP",
                                          "HAS_POPULATION_OBSERVATION"],
            "dynamic_analytical_computation": ["accessibility counts", "nearest times"],
        }
        return answer

    def provenance(self, entity_id: str) -> dict:
        if entity_id in RELATION_TYPES:
            result = relation_provenance(entity_id)
        else:
            result = entity_provenance(self.graph.get_node(entity_id))
        result["data_mode"] = result.get("data_mode") or self.graph.metadata.get("mode")
        return result

    def status(self) -> dict:
        metadata = dict(self.graph.metadata)
        return {
            "available": self.available,
            "mode": metadata.get("mode"),
            "data_state": runtime_snapshot().block("spatial_graph", metadata.get("mode")),
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
