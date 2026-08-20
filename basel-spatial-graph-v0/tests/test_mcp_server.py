"""In-process FastMCP protocol tests (run in the optional Python 3.10+ env)."""
import asyncio

import pytest

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client

from app.mcp import server
from app.mcp.tools import SpatialGraphMCPTools
from app.spatial_graph.fixtures import fixture_service


def call(coro):
    return asyncio.run(coro)


async def invoke(name, arguments):
    server._tools = SpatialGraphMCPTools(fixture_service())
    async with Client(server.mcp) as client:
        return await client.call_tool(name, arguments)


def test_server_registers_exactly_five_typed_tools():
    async def inspect():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    listed = call(inspect())
    assert {tool.name for tool in listed} == {
        "describe_graph", "query_graph", "find_reachable", "compare_areas", "get_provenance",
    }
    for tool in listed:
        assert tool.description
        assert tool.inputSchema["type"] == "object"


def test_protocol_schema_discovery_m1():
    result = call(invoke("describe_graph", {})).data
    assert {"Neighborhood", "PopulationObservation", "ServiceLocation",
            "ServiceCategory", "TransitStop", "TransitRoute"}.issubset(result["entity_types"])


def test_protocol_grouped_statistics_m2():
    result = call(invoke("query_graph", {"query": {
        "start": {"type": "PopulationObservation"},
        "group_by": ["year"],
        "aggregate": [{"function": "sum", "field": "children", "as": "children_total"}],
        "order_by": [{"field": "year", "direction": "asc"}],
    }})).data
    assert result["results"] == [
        {"year": 2024, "children_total": 2400},
        {"year": 2025, "children_total": 2520},
    ]


def test_protocol_graph_and_relational_queries_m3_m4():
    demographic = call(invoke("query_graph", {"query": {
        "start": {"type": "Neighborhood",
                  "filters": [{"field": "children", "op": "gt", "value": 1000}]},
        "return": ["Neighborhood.name", "Neighborhood.children"],
    }})).data
    assert demographic["count"] == 1

    relational = call(invoke("query_graph", {"query": {
        "start": {"type": "ServiceLocation",
                  "filters": [{"field": "neighborhood_id", "op": "eq", "value": "area:a"}]},
        "group_by": ["category"],
        "aggregate": [{"function": "count", "as": "service_count"}],
    }})).data
    assert relational["count"] > 1


def test_protocol_dynamic_and_comparison_m5_m6():
    reachable = call(invoke("find_reachable", {
        "neighborhood_id": "area:a", "category": "pharmacy",
        "mode": "walk", "minutes": 15,
    })).data
    assert reachable["category"] == "pharmacy"
    assert reachable["provenance"]["classification"] == "dynamic"

    comparison = call(invoke("compare_areas", {
        "neighborhood_ids": ["area:a", "area:b"], "category": "pharmacy",
        "modes": ["walk", "bike"], "minutes": 15,
    })).data
    assert comparison["count"] == 4


def test_protocol_provenance_m7_and_compact_defaults():
    result = call(invoke("get_provenance", {"identifier": "REACHABLE_WITHIN"})).data
    assert result["classification"] == "dynamic"
    assert result["persisted"] is False
    assert "geometry" not in str(result).lower()
