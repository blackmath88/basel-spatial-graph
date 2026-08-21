"""Offline contract tests for grouped structured queries."""
import copy

import pytest

from app.errors import QuerySpecError
from app.spatial_graph import SpatialGraphService
from app.spatial_graph.fixtures import fixture_service


@pytest.fixture(scope="module")
def service():
    return fixture_service()


def population_query(**extra):
    query = {
        "start": {"type": "PopulationObservation"},
        "group_by": ["year"],
        "aggregate": [
            {"function": "sum", "field": "children", "as": "children_total"},
        ],
        "return": ["year", "children_total"],
    }
    query.update(extra)
    return query


def test_one_grouping_key_and_sum(service):
    result = service.query(population_query(order_by=[{"field": "year", "direction": "asc"}]))
    assert result["results"] == [
        {"year": 2024, "children_total": 2400},
        {"year": 2025, "children_total": 2520},
    ]


def test_multiple_grouping_keys(service):
    result = service.query({
        "start": {"type": "PopulationObservation"},
        "group_by": ["year", "neighborhood_id"],
        "aggregate": [{"function": "count", "as": "observations"}],
        "order_by": [
            {"field": "year", "direction": "asc"},
            {"field": "neighborhood_id", "direction": "asc"},
        ],
    })
    assert len(result["results"]) == 4
    assert all(row["observations"] == 1 for row in result["results"])


def test_count_and_count_distinct(service):
    result = service.query({
        "start": {"type": "ServiceLocation"},
        "group_by": ["category"],
        "aggregate": [
            {"function": "count", "as": "rows"},
            {"function": "count_distinct", "field": "id", "as": "unique_services"},
        ],
        "having": [{"field": "rows", "op": "gte", "value": 2}],
        "order_by": [{"field": "rows", "direction": "desc"}],
    })
    assert result["results"][0] == {"category": "grocery", "rows": 3,
                                     "unique_services": 3}
    assert all(row["rows"] >= 2 for row in result["results"])


def test_avg_min_and_max(service):
    result = service.query({
        "start": {"type": "PopulationObservation"},
        "group_by": ["year"],
        "aggregate": [
            {"function": "avg", "field": "children", "as": "children_avg"},
            {"function": "min", "field": "children", "as": "children_min"},
            {"function": "max", "field": "children", "as": "children_max"},
        ],
        "order_by": [{"field": "year", "direction": "asc"}],
    })
    assert result["results"][0] == {
        "year": 2024, "children_avg": 1200, "children_min": 400, "children_max": 2000,
    }


def test_aliases_work_in_having_order_and_return(service):
    result = service.query(population_query(**{
        "having": [{"field": "children_total", "op": "gt", "value": 2400}],
        "order_by": [{"field": "children_total", "direction": "desc"}],
        "return": ["children_total", "year"],
    }))
    assert result["results"] == [{"children_total": 2520, "year": 2025}]


def test_group_limit_is_applied_after_ordering(service):
    result = service.query(population_query(
        order_by=[{"field": "children_total", "direction": "desc"}], limit=1))
    assert result["results"] == [{"year": 2025, "children_total": 2520}]
    assert result["truncated"] is True


def test_missing_values_are_ignored_and_empty_numeric_group_is_null():
    original = fixture_service(with_engines=False)
    graph = copy.deepcopy(original.graph)
    observations = list(graph.nodes_of_type("PopulationObservation"))
    first_year = min(row["year"] for row in observations)
    for observation in observations:
        if observation["year"] == first_year:
            graph.graph.nodes[observation["id"]]["children"] = None
    service = SpatialGraphService(graph)
    result = service.query(population_query(order_by=[{"field": "year", "direction": "asc"}]))
    assert result["results"][0]["children_total"] is None
    assert result["results"][1]["children_total"] == 2520


def test_invalid_numeric_aggregate_type_has_recovery_fields():
    with pytest.raises(QuerySpecError) as error:
        fixture_service().query({
            "start": {"type": "ServiceLocation"},
            "group_by": ["category"],
            "aggregate": [{"function": "sum", "field": "name", "as": "bad"}],
        })
    assert error.value.details["field"] == "name"
    assert "lat" in error.value.details["valid_fields"]


def test_grouped_return_is_schema_validated(service):
    with pytest.raises(QuerySpecError) as error:
        service.query(population_query(**{"return": ["year", "made_up_total"]}))
    assert "children_total" in error.value.details["known"]


def test_grouped_provenance_and_execution_trace(service):
    result = service.query(population_query())
    assert result["provenance"]["aggregation"]["classification"] == "derived"
    assert result["provenance"]["aggregation"]["computations"][0]["field"] == "children"
    assert result["provenance"]["datasets"]
    assert result["execution"]["rows_scanned"] == 4
    assert result["execution"]["groups_formed"] == 2
    assert result["execution"]["groups_returned"] == 2
    field = result["provenance"]["fields"]["results[].children_total"]
    computation = result["provenance"]["computations"][field["computation_ref"]]
    assert field["classification"] == "derived"
    assert computation["method"] == "sum"
    assert computation["input_field"] == "children"
    assert computation["source_refs"] == ["population"]


def test_grouping_after_traversal(service):
    result = service.query({
        "start": {"type": "TransitStop"},
        "traverse": [{"relation": "LOCATED_IN", "target_type": "Neighborhood",
                      "as": "areas", "min_count": 1}],
        "group_by": ["areas.name"],
        "aggregate": [{"function": "count", "field": "id", "as": "stop_count"}],
        "order_by": [{"field": "stop_count", "direction": "desc"}],
    })
    assert result["results"] == [{"areas.name": "Fixture East", "stop_count": 2}]


def test_legacy_aggregate_and_rank_remain_compatible(service):
    result = service.query({
        "start": {"type": "Neighborhood"},
        "traverse": [{"relation": "HAS_SERVICE", "as": "services"}],
        "aggregate": {"service_count": {"op": "count", "of": "services"}},
        "rank": {"by": "service_count", "order": "desc"},
        "return": ["Neighborhood.name", "service_count"],
    })
    assert result["results"][0]["service_count"] >= result["results"][1]["service_count"]
