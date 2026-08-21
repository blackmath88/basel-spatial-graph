"""Thin, framework-independent functions exposed by the MCP server.

No graph or routing logic belongs here. Keeping this class independent of
FastMCP also lets the Python 3.9 application test the complete adapter offline;
`server.py` is only the protocol registration layer.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import BaselGraphError, QuerySpecError
from ..modes import parse_mode
from ..service_model import parse_category
from ..spatial_graph import SpatialGraphService
from ..spatial_graph.provenance import shared_provenance
from ..data_quality import relevant_caveats


class SpatialGraphMCPTools:
    """Bounded agent operations backed by one `SpatialGraphService`."""

    def __init__(self, service: SpatialGraphService):
        self.service = service

    @staticmethod
    def _error(error: BaselGraphError) -> dict:
        return {"error": error.code, "message": error.message, **error.details}

    def describe_graph(self) -> dict:
        schema = self.service.schema()
        return {
            "graph": schema["graph"],
            "entity_types": schema["entity_types"],
            "relations": schema["relations"],
            "filter_operators": schema["filter_operators"],
            "analyses": schema["analyses"],
            "query_language": schema["query_language"],
            "output_defaults": {"geometry": "excluded", "max_results": 1000},
        }

    def query_graph(self, query: Dict[str, Any]) -> dict:
        try:
            return self.service.query(query)
        except BaselGraphError as error:
            return self._error(error)

    def _neighborhood(self, neighborhood_id: str) -> dict:
        result = self.service.entity("Neighborhood", neighborhood_id)
        return self.service.graph.get_node(result["id"])

    def find_reachable(self, neighborhood_id: str, category: str,
                       mode: str = "walk", minutes: float = 15,
                       departure_time: Optional[str] = None,
                       max_transfers: int = 1) -> dict:
        """Run the trusted accessibility engine from a neighbourhood origin."""
        try:
            if not 0 < float(minutes) <= 60:
                raise QuerySpecError("'minutes' must be greater than 0 and at most 60.")
            parse_mode(mode)
            parse_category(category)
            node = self._neighborhood(neighborhood_id)
            result = self.service.analysis.accessibility(
                node, mode=mode, minutes=minutes, category=category,
                departure_time=departure_time, max_transfers=max_transfers)
            return {
                "origin": {"neighborhood_id": neighborhood_id,
                           "name": node.get("name"), **result["origin"]},
                "category": result["category"], "mode": result["mode"],
                "minutes": result["minutes"], "count": result["count"],
                "nearest_minutes": result["nearest_minutes"],
                "nearest_name": result["nearest_name"],
                "departure_time": result.get("departure_time"),
                "service_date": result.get("service_date"),
                "provenance": {**shared_provenance(
                    self.service.graph, types=["Neighborhood"],
                    analyses=[{"classification": "dynamic", "computed_by":
                               "app.accessibility / app.multimodal"}],
                    computations=[{"id": "accessibility", **result["provenance"]}],
                    fields={
                        "count": {"classification": "dynamic",
                                  "computation_ref": "accessibility"},
                        "nearest_minutes": {"classification": "dynamic",
                                            "computation_ref": "accessibility"},
                    },
                    quality=relevant_caveats(
                        self.service.graph.metadata.get("data_quality"),
                        categories=[category], networks=["walk" if mode == "transit" else mode],
                        transit=mode == "transit")),
                    "classification": "dynamic",
                    "computed_by": "app.accessibility / app.multimodal",
                    "parameters": {"mode": mode, "minutes": float(minutes),
                                   "category": category, "departure_time": departure_time,
                                   "max_transfers": max_transfers}},
            }
        except BaselGraphError as error:
            return self._error(error)

    def compare_areas(self, neighborhood_ids: List[str], category: str,
                      modes: Optional[List[str]] = None, minutes: float = 15,
                      departure_time: Optional[str] = None) -> dict:
        """Compare the same category and budget across areas and travel modes."""
        try:
            if not neighborhood_ids:
                raise QuerySpecError("Provide at least one neighborhood_id.")
            if len(neighborhood_ids) > 25:
                raise QuerySpecError("At most 25 neighborhoods may be compared.")
            wanted_modes = modes or self.service.analysis.available_modes
            for mode in wanted_modes:
                parse_mode(mode)
            parse_category(category)
            rows = []
            computations = {}
            for entity_id in neighborhood_ids:
                node = self._neighborhood(entity_id)
                for mode in wanted_modes:
                    result = self.service.analysis.accessibility(
                        node, mode=mode, minutes=minutes, category=category,
                        departure_time=departure_time)
                    computations.setdefault(mode, {"id": mode, **result["provenance"]})
                    rows.append({
                        "neighborhood_id": entity_id, "neighborhood": node.get("name"),
                        "mode": mode, "category": category, "minutes": float(minutes),
                        "count": result["count"],
                        "nearest_minutes": result["nearest_minutes"],
                        "nearest_name": result["nearest_name"],
                    })
            return {
                "results": rows, "count": len(rows),
                "execution": {"areas": len(neighborhood_ids), "modes": wanted_modes,
                              "analysis_calls": len(rows), "bounded": True},
                "provenance": {**shared_provenance(
                    self.service.graph, types=["Neighborhood"],
                    analyses=[{"classification": "dynamic", "computed_by":
                               "app.accessibility / app.multimodal"}],
                    computations=list(computations.values()),
                    fields={
                        "results[].count": {"classification": "dynamic",
                                            "computation_refs": list(computations)},
                        "results[].nearest_minutes": {"classification": "dynamic",
                                                      "computation_refs": list(computations)},
                    },
                    quality=relevant_caveats(
                        self.service.graph.metadata.get("data_quality"), categories=[category],
                        networks=["walk" if mode == "transit" else mode for mode in wanted_modes],
                        transit="transit" in wanted_modes)),
                    "classification": "dynamic",
                    "computed_by": "app.accessibility / app.multimodal",
                    "parameters": {"category": category, "minutes": float(minutes),
                                   "departure_time": departure_time}},
            }
        except BaselGraphError as error:
            return self._error(error)

    def get_provenance(self, identifier: str) -> dict:
        """Return entity or relation provenance; query-result IDs are not persisted yet."""
        try:
            result = self.service.provenance(identifier)
            result["limitations"] = (
                "Query results are request-scoped and have no persistent ID; inspect the "
                "provenance block returned by query_graph or find_reachable.")
            return result
        except BaselGraphError as error:
            return self._error(error)


def load_default_tools() -> SpatialGraphMCPTools:
    """Load the same prepared service used by the CLI, without HTTP."""
    from ..spatial_graph.cli import _service

    return SpatialGraphMCPTools(_service())
