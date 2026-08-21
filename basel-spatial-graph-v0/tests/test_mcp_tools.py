"""Offline tests for the framework-independent MCP adapter."""
import pytest

from app.mcp import SpatialGraphMCPTools
from app.spatial_graph.fixtures import fixture_service


@pytest.fixture(scope="module")
def tools():
    return SpatialGraphMCPTools(fixture_service())


def test_describe_graph_is_compact_and_discoverable(tools):
    result = tools.describe_graph()
    assert "Neighborhood" in result["entity_types"]
    assert "PopulationObservation" in result["entity_types"]
    assert result["relations"]["REACHABLE_WITHIN"]["persisted"] is False
    assert "sum" in result["query_language"]["aggregate_functions"]


def test_query_graph_and_grouping(tools):
    result = tools.query_graph({
        "start": {"type": "PopulationObservation"},
        "group_by": ["year"],
        "aggregate": [{"function": "sum", "field": "children", "as": "children_total"}],
        "order_by": [{"field": "year", "direction": "asc"}],
    })
    assert result["results"][0] == {"year": 2024, "children_total": 2400}
    assert result["provenance"]["aggregation"]["classification"] == "derived"


def test_query_error_has_recovery_metadata(tools):
    result = tools.query_graph({"start": {"type": "Neighborhood",
                                          "filters": [{"field": "kids", "op": "gt",
                                                       "value": 1}]}})
    assert result["error"] == "invalid_query"
    assert "children" in result["known"]


def test_find_reachable_uses_fixture_engine_without_geometry(tools):
    result = tools.find_reachable("area:a", "pharmacy", "walk", 15)
    assert result["category"] == "pharmacy"
    assert result["provenance"]["classification"] == "dynamic"
    computation = result["provenance"]["computations"]["accessibility"]
    assert computation["algorithm"] == "NetworkX single-source Dijkstra"
    assert computation["speed_kmh"] > 0
    assert computation["network"]["fixture"] is True
    assert computation["source_refs"]
    assert "geometry" not in str(result).lower()


def test_find_reachable_validates_modes_for_recovery(tools):
    result = tools.find_reachable("area:a", "pharmacy", "flying", 15)
    assert result["error"] == "unknown_mode"
    assert "known" in result


def test_compare_areas_and_modes_is_bounded(tools):
    result = tools.compare_areas(["area:a", "area:b"], "pharmacy", ["walk", "bike"], 15)
    assert result["count"] == 4
    assert result["execution"]["bounded"] is True
    assert {row["mode"] for row in result["results"]} == {"walk", "bike"}
    assert set(result["provenance"]["computations"]) == {"walk", "bike"}
    assert all(row["algorithm"] == "NetworkX single-source Dijkstra"
               for row in result["provenance"]["computations"].values())


def test_entity_and_relation_provenance(tools):
    entity = tools.get_provenance("population:a:2025")
    relation = tools.get_provenance("REACHABLE_WITHIN")
    assert entity["classification"] == "official"
    assert entity["data_mode"] == "fixture"
    assert entity["reference_year"] == 2025
    assert relation["persisted"] is False
    assert relation["classification"] == "dynamic"
