"""FastMCP protocol registration for the Basel Spatial Graph.

Start locally with `python -m app.mcp.server` (stdio). FastMCP is deliberately
an optional Python 3.10+ dependency; the V0.4 application remains on its current
Python floor and dependency set.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from fastmcp import FastMCP
except ImportError as error:  # pragma: no cover - exercised by startup smoke tests
    raise RuntimeError(
        "The MCP adapter needs Python 3.10+ and the optional MCP dependencies. "
        "See docs/MCP.md for isolated-environment setup."
    ) from error

from .tools import SpatialGraphMCPTools, load_default_tools

mcp = FastMCP("Basel Spatial Graph")
_tools: Optional[SpatialGraphMCPTools] = None


def tools() -> SpatialGraphMCPTools:
    global _tools
    if _tools is None:
        _tools = load_default_tools()
    return _tools


@mcp.tool
def describe_graph() -> Dict[str, Any]:
    """Discover the Basel graph before querying it.

    Use when entity fields, relation directions, persisted-vs-dynamic status,
    operators, or query features are unknown. Returns compact schema metadata;
    it does not return graph rows or geometry.
    """
    return tools().describe_graph()


@mcp.tool
def query_graph(query: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a bounded structured relational query against the prepared graph.

    Use for typed filtering, traversal, grouping, aggregation and graph-backed
    analytical constraints. Do not pass natural language, SQL, Cypher or Python.
    Call describe_graph first when the schema is unknown. Geometry is excluded
    unless the structured query explicitly opts in.
    """
    return tools().query_graph(query)


@mcp.tool
def find_reachable(neighborhood_id: str, category: str, mode: str = "walk",
                   minutes: float = 15, departure_time: Optional[str] = None,
                   max_transfers: int = 1) -> Dict[str, Any]:
    """Find services reachable from a neighbourhood's documented origin.

    Use for walk, bike or schedule-aware transit reachability for one service
    category and a 1-60 minute budget. Transit may use departure_time. Returns
    compact counts, nearest service and auditable computation metadata, never
    route geometry.
    """
    return tools().find_reachable(neighborhood_id, category, mode, minutes,
                                  departure_time, max_transfers)


@mcp.tool
def compare_areas(neighborhood_ids: List[str], category: str,
                  modes: Optional[List[str]] = None, minutes: float = 15,
                  departure_time: Optional[str] = None) -> Dict[str, Any]:
    """Compare accessibility consistently across up to 25 areas and modes.

    Use for area comparisons or walking/cycling/transit gains for one category.
    Every row uses the same budget and category. Returns compact measurements,
    not a generic statistical model or geometry.
    """
    return tools().compare_areas(neighborhood_ids, category, modes, minutes,
                                 departure_time)


@mcp.tool
def get_provenance(identifier: str) -> Dict[str, Any]:
    """Inspect exact provenance for an entity ID or declared relation name.

    Use after discovery or querying to identify datasets, dates,
    classification and computation. Query results are request-scoped, so their
    provenance is returned inline by query_graph/find_reachable rather than by ID.
    """
    return tools().get_provenance(identifier)


if __name__ == "__main__":
    mcp.run(transport="stdio")
