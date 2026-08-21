"""Spatial Graph Core: schema, typing, structure, querying and provenance.

Everything runs on the synthetic graph from `app/spatial_graph/fixtures.py`, so
no prepared artefact and no live Basel API is involved.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.errors import (
    QuerySpecError,
    UnknownEntityError,
    UnknownEntityTypeError,
    UnknownRelationError,
)
from app.main import app
from app.spatial_graph import NetworkXSpatialGraph, QuerySpec, describe_schema
from app.spatial_graph.fixtures import fixture_service
from app.spatial_graph.schema import NODE_TYPES, RELATION_TYPES


@pytest.fixture(scope="module")
def service():
    return fixture_service()


@pytest.fixture(scope="module")
def graph(service):
    return service.graph


@pytest.fixture
def client():
    return TestClient(app)


def run(service, spec: dict) -> dict:
    return service.query(spec)


# --- schema -------------------------------------------------------------------
def test_the_schema_describes_every_node_type():
    described = describe_schema()
    assert set(described["entity_types"]) == {
        "Neighborhood", "PopulationObservation", "ServiceCategory", "ServiceLocation",
        "StreetAccessPoint", "TransitStop", "TransitRoute",
    }
    for name, entry in described["entity_types"].items():
        assert entry["fields"], name
        assert entry["source"], name
        assert entry["id_prefix"], name


def test_relations_declare_their_endpoints_and_whether_they_persist():
    described = describe_schema()
    located = described["relations"]["LOCATED_IN"]
    assert located["from"] == ["ServiceLocation", "TransitStop"]
    assert located["to"] == ["Neighborhood"]
    assert located["persisted"] is True
    reachable = described["relations"]["REACHABLE_WITHIN"]
    assert reachable["persisted"] is False
    assert reachable["computed_by"]


def test_every_relation_endpoint_is_a_known_node_type():
    for relation in RELATION_TYPES.values():
        for name in list(relation.sources) + list(relation.targets):
            assert name in NODE_TYPES, f"{relation.name} points at unknown '{name}'"


def test_declared_inverses_are_symmetric():
    """A relation may have several inverses, but each must point back."""
    for relation in RELATION_TYPES.values():
        for name in relation.inverses:
            other = RELATION_TYPES.get(name)
            assert other is not None, f"{relation.name} names unknown inverse '{name}'"
            assert relation.name in other.inverses, f"{name} does not invert {relation.name}"


def test_the_schema_reports_live_counts(service):
    described = service.schema()
    assert described["entity_types"]["Neighborhood"]["count"] == 2
    assert described["relations"]["HAS_SERVICE"]["count"] > 0
    assert described["analyses"]["accessibility"]["available_modes"]


# --- node typing --------------------------------------------------------------
def test_every_node_carries_a_declared_type(graph):
    for _, data in graph.graph.nodes(data=True):
        assert data.get("type") in NODE_TYPES
        assert data.get("id")


def test_node_ids_use_their_declared_prefix(graph):
    for _, data in graph.graph.nodes(data=True):
        prefix = NODE_TYPES[data["type"]].id_prefix
        assert data["id"].startswith(prefix), data["id"]


def test_every_edge_uses_a_declared_relation_between_declared_types(graph):
    for source, target, relation in graph.graph.edges(keys=True):
        assert relation in RELATION_TYPES, relation
        spec = RELATION_TYPES[relation]
        assert graph.graph.nodes[source]["type"] in spec.sources
        assert graph.graph.nodes[target]["type"] in spec.targets


def test_no_analytical_relation_is_persisted(graph):
    analytical = {name for name, r in RELATION_TYPES.items() if r.kind != "structural"}
    assert analytical
    assert analytical.isdisjoint(graph.edge_counts())


# --- structural relations -----------------------------------------------------
def test_services_are_linked_to_their_neighbourhood(graph):
    services = [n for n in graph.nodes_of_type("ServiceLocation") if n.get("neighborhood_id")]
    assert services
    for service in services:
        targets = {row["node"]["id"] for row in graph.neighbors(service["id"], "LOCATED_IN")}
        assert service["neighborhood_id"] in targets
        back = {row["node"]["id"] for row in
                graph.neighbors(service["neighborhood_id"], "HAS_SERVICE")}
        assert service["id"] in back


def test_every_service_belongs_to_exactly_one_category(graph):
    for service in graph.nodes_of_type("ServiceLocation"):
        categories = graph.neighbors(service["id"], "OF_CATEGORY")
        assert len(categories) == 1
        assert categories[0]["node"]["category"] == service["category"]


def test_neighbourhood_adjacency_is_symmetric(graph):
    for area in graph.nodes_of_type("Neighborhood"):
        for row in graph.neighbors(area["id"], "ADJACENT_TO"):
            back = {r["node"]["id"] for r in graph.neighbors(row["node"]["id"], "ADJACENT_TO")}
            assert area["id"] in back


def test_transit_stops_link_to_neighbourhoods_and_routes(graph):
    stops = list(graph.nodes_of_type("TransitStop"))
    assert stops
    served = [s for s in stops if graph.neighbors(s["id"], "SERVED_BY")]
    assert served
    for stop in served:
        for row in graph.neighbors(stop["id"], "SERVED_BY"):
            back = {r["node"]["id"] for r in graph.neighbors(row["node"]["id"], "SERVES")}
            assert stop["id"] in back


def test_access_points_carry_their_network(graph):
    points = list(graph.nodes_of_type("StreetAccessPoint"))
    assert {p["network"] for p in points} == {"walk", "bike"}
    for point in points:
        assert point["attached_count"] >= 1


# --- demographics -------------------------------------------------------------
def test_population_observations_keep_the_year_dimension(graph):
    observations = list(graph.nodes_of_type("PopulationObservation"))
    assert observations
    years = {o["year"] for o in observations}
    assert len(years) > 1, "several years must be preserved, not just the latest"
    for observation in observations:
        assert observation["total"] >= observation["children"]
        assert observation["total"] >= observation["elderly"]
        assert observation["young"] >= observation["children"]


def test_the_latest_year_is_denormalised_onto_the_neighbourhood(graph):
    latest = max(o["year"] for o in graph.nodes_of_type("PopulationObservation"))
    for area in graph.nodes_of_type("Neighborhood"):
        assert area["reference_year"] == latest
        observation = next(
            o for o in graph.nodes_of_type("PopulationObservation")
            if o["neighborhood_id"] == area["id"] and o["year"] == latest)
        assert area["population_total"] == observation["total"]
        assert area["children"] == observation["children"]


def test_derived_population_shares_are_consistent(graph):
    for area in graph.nodes_of_type("Neighborhood"):
        expected = round(area["children"] / area["population_total"], 4)
        assert area["child_share"] == pytest.approx(expected, abs=1e-4)


def test_population_normalisation_from_the_source_shape():
    from app.population import AGE_GROUPS, fixture_population

    data = fixture_population()
    assert set(AGE_GROUPS) == {"total", "children", "young", "working_age",
                               "elderly", "elderly_80_plus"}
    assert data["provenance"]["age_group_definitions"]["children"] == "aged 0-17 (minors)"
    assert data["latest_year"] == max(o["year"] for o in data["observations"])


def test_every_neighbourhood_has_a_documented_origin(graph):
    for area in graph.nodes_of_type("Neighborhood"):
        assert area["representative_lat"] is not None
        assert area["representative_lon"] is not None
        assert "representative point" in area["origin_method"]


# --- storage ------------------------------------------------------------------
def test_the_graph_round_trips_through_its_artefact(tmp_path, graph):
    path = graph.save(tmp_path / "graph.json")
    reloaded = NetworkXSpatialGraph.load(path)
    assert reloaded.node_counts() == graph.node_counts()
    assert reloaded.edge_counts() == graph.edge_counts()
    assert reloaded.metadata["origin_method"] == graph.metadata["origin_method"]


def test_an_unreadable_artefact_is_reported_cleanly(tmp_path):
    from app.errors import SpatialGraphUnavailableError

    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SpatialGraphUnavailableError):
        NetworkXSpatialGraph.load(path)
    with pytest.raises(SpatialGraphUnavailableError):
        NetworkXSpatialGraph.load(tmp_path / "absent.json")


# --- retrieval ----------------------------------------------------------------
def test_listing_entities_excludes_geometry_by_default(service):
    rows = service.entities("Neighborhood", limit=5)["results"]
    assert rows
    assert all("geometry" not in row for row in rows)
    with_geometry = service.entities("Neighborhood", limit=1, include_geometry=True)["results"]
    assert "geometry" in with_geometry[0]


def test_results_are_bounded(service):
    result = service.entities("ServiceLocation", limit=3)
    assert len(result["results"]) == 3
    assert result["total"] >= 3


def test_unknown_types_relations_and_ids_are_clean_errors(service):
    with pytest.raises(UnknownEntityTypeError):
        service.entities("Dragon")
    with pytest.raises(UnknownEntityError):
        service.entity("Neighborhood", "area:nope")
    with pytest.raises(UnknownRelationError):
        service.neighbors("Neighborhood", "area:a", relation="TELEPORTS_TO")


def test_subgraph_is_depth_and_size_bounded(service):
    shallow = service.subgraph("Neighborhood", "area:a", depth=1)
    deep = service.subgraph("Neighborhood", "area:a", depth=3)
    assert deep["node_count"] >= shallow["node_count"]
    tiny = service.subgraph("Neighborhood", "area:a", depth=3, limit=5)
    assert tiny["node_count"] <= 5 and tiny["truncated"]


# --- query: filters -----------------------------------------------------------
@pytest.mark.parametrize("op,value,expected", [
    ("gt", 5000, {"Fixture West"}),
    ("lt", 5000, {"Fixture East"}),
    ("gte", 4100, {"Fixture West", "Fixture East"}),
    ("eq", 10200, {"Fixture West"}),
    ("ne", 10200, {"Fixture East"}),
    ("between", [4000, 5000], {"Fixture East"}),
    ("in", [4100], {"Fixture East"}),
    ("not_in", [4100], {"Fixture West"}),
])
def test_filter_operators(service, op, value, expected):
    result = run(service, {"start": {"type": "Neighborhood",
                                     "filters": [{"field": "population_total", "op": op,
                                                  "value": value}]},
                           "return": ["Neighborhood.name"]})
    assert {row["Neighborhood.name"] for row in result["results"]} == expected


def test_text_and_existence_filters(service):
    contains = run(service, {"start": {"type": "Neighborhood",
                                       "filters": [{"field": "name", "op": "contains",
                                                    "value": "west"}]},
                             "return": ["Neighborhood.name"]})
    assert [r["Neighborhood.name"] for r in contains["results"]] == ["Fixture West"]
    exists = run(service, {"start": {"type": "ServiceLocation",
                                     "filters": [{"field": "name", "op": "exists"}]}})
    assert exists["count"] >= 1


def test_unknown_fields_and_operators_are_refused(service):
    with pytest.raises(QuerySpecError):
        run(service, {"start": {"type": "Neighborhood",
                                "filters": [{"field": "vibes", "op": "eq", "value": 1}]}})
    with pytest.raises(QuerySpecError):
        run(service, {"start": {"type": "Neighborhood",
                                "filters": [{"field": "name", "op": "regex", "value": ".*"}]}})


def test_a_query_needs_a_known_start_type(service):
    from app.errors import BaselGraphError

    with pytest.raises(BaselGraphError):
        run(service, {"start": {"type": "Dragon"}})
    with pytest.raises(QuerySpecError):
        run(service, {})


# --- query: traversal ---------------------------------------------------------
def test_traversal_collects_typed_neighbours(service):
    result = run(service, {
        "start": {"type": "Neighborhood"},
        "traverse": [{"relation": "HAS_SERVICE", "target_type": "ServiceLocation",
                      "as": "groceries",
                      "filters": [{"field": "category", "op": "eq", "value": "grocery"}]}],
        "return": ["Neighborhood.name", "groceries.count"],
    })
    assert result["results"]
    assert sum(row["groceries.count"] for row in result["results"]) >= 1
    assert result["execution"]["relations_traversed"] == ["HAS_SERVICE"]


def test_traversal_can_chain_from_a_previous_step(service):
    result = run(service, {
        "start": {"type": "Neighborhood"},
        "traverse": [
            {"relation": "HAS_TRANSIT_STOP", "target_type": "TransitStop", "as": "stops"},
            {"relation": "SERVED_BY", "target_type": "TransitRoute", "as": "routes",
             "from": "stops"},
        ],
        "return": ["Neighborhood.name", "stops.count", "routes.count"],
    })
    assert any(row["routes.count"] > 0 for row in result["results"])


def test_min_count_acts_as_a_constraint(service):
    demanding = run(service, {
        "start": {"type": "Neighborhood"},
        "traverse": [{"relation": "HAS_SERVICE", "as": "services", "min_count": 99}],
    })
    assert demanding["count"] == 0


def test_analytical_relations_cannot_be_traversed(service):
    with pytest.raises(QuerySpecError) as excinfo:
        run(service, {"start": {"type": "Neighborhood"},
                      "traverse": [{"relation": "REACHABLE_WITHIN"}]})
    assert "analytical" in excinfo.value.message


def test_a_relation_must_point_at_the_requested_type(service):
    with pytest.raises(QuerySpecError):
        run(service, {"start": {"type": "Neighborhood"},
                      "traverse": [{"relation": "HAS_SERVICE", "target_type": "TransitRoute"}]})


def test_traverse_depth_is_bounded(service):
    with pytest.raises(QuerySpecError):
        run(service, {"start": {"type": "Neighborhood"},
                      "traverse": [{"relation": "HAS_SERVICE"}] * 9})


# --- query: aggregation, ranking, projection ----------------------------------
def test_aggregate_and_rank(service):
    result = run(service, {
        "start": {"type": "Neighborhood"},
        "traverse": [{"relation": "HAS_SERVICE", "as": "services"}],
        "aggregate": {"service_count": {"op": "count", "of": "services"}},
        "rank": {"by": "service_count", "order": "desc"},
        "return": ["Neighborhood.name", "service_count"],
    })
    counts = [row["service_count"] for row in result["results"]]
    assert counts == sorted(counts, reverse=True)


def test_limit_bounds_the_answer(service):
    result = run(service, {"start": {"type": "ServiceLocation"}, "limit": 2})
    assert result["count"] == 2
    assert result["truncated"] is True


def test_geometry_is_excluded_unless_requested(service):
    without = run(service, {"start": {"type": "ServiceLocation"}, "limit": 1})
    assert "geometry" not in without["results"][0]
    with_geometry = run(service, {"start": {"type": "ServiceLocation"}, "limit": 1,
                                  "include_geometry": True})
    assert "geometry" in with_geometry["results"][0]


# --- query: dynamic accessibility ---------------------------------------------
def test_an_accessibility_constraint_filters_rows(service):
    generous = run(service, {
        "start": {"type": "Neighborhood"},
        "analyses": [{"as": "walk15", "analysis": "accessibility", "mode": "walk",
                      "minutes": 15, "target_category": "grocery",
                      "operator": "count_gte", "value": 0}],
        "return": ["Neighborhood.name", "walk15.count"],
    })
    impossible = run(service, {
        "start": {"type": "Neighborhood"},
        "analyses": [{"as": "walk15", "analysis": "accessibility", "mode": "walk",
                      "minutes": 15, "target_category": "grocery",
                      "operator": "count_gt", "value": 999}],
    })
    assert generous["count"] >= 1
    assert impossible["count"] == 0


def test_accessibility_results_are_marked_dynamic(service):
    result = run(service, {
        "start": {"type": "Neighborhood"},
        "analyses": [{"as": "walk15", "analysis": "accessibility", "mode": "walk",
                      "minutes": 15, "target_category": "grocery"}],
        "return": ["Neighborhood.name", "walk15.count", "walk15.kind", "walk15.origin"],
    })
    row = result["results"][0]
    assert row["walk15.kind"] == "dynamic analytical computation"
    assert "representative point" in row["walk15.origin"]["method"]


def test_different_modes_give_different_answers(service):
    def count(mode):
        result = run(service, {
            "start": {"type": "Neighborhood", "ids": ["area:a"]},
            "analyses": [{"as": "a", "analysis": "accessibility", "mode": mode, "minutes": 15}],
            "return": ["a.count"],
        })
        return result["results"][0]["a.count"]

    assert count("bike") >= count("walk")


def test_analysis_results_are_cached_across_a_query(service):
    service.analysis.clear_cache()
    result = run(service, {
        "start": {"type": "Neighborhood"},
        "analyses": [
            {"as": "first", "analysis": "accessibility", "mode": "walk", "minutes": 15},
            {"as": "second", "analysis": "accessibility", "mode": "walk", "minutes": 15},
        ],
        "return": ["first.count", "second.count"],
    })
    engine = result["provenance"]["analysis_engine"]
    assert engine["cache_hits"] >= 1
    assert result["results"][0]["first.count"] == result["results"][0]["second.count"]


def test_an_unknown_analysis_is_refused(service):
    with pytest.raises(QuerySpecError):
        run(service, {"start": {"type": "Neighborhood"},
                      "analyses": [{"analysis": "astrology"}]})


def test_accessibility_only_applies_to_neighbourhoods(service):
    with pytest.raises(QuerySpecError):
        run(service, {"start": {"type": "ServiceLocation"},
                      "analyses": [{"analysis": "accessibility", "mode": "walk"}]})


def test_a_query_without_an_engine_says_so(graph):
    from app.spatial_graph import SpatialGraphService

    bare = SpatialGraphService(graph, engines={})
    with pytest.raises(QuerySpecError):
        bare.query({"start": {"type": "Neighborhood"},
                    "analyses": [{"analysis": "accessibility", "mode": "walk"}]})


# --- provenance ---------------------------------------------------------------
def test_query_results_explain_themselves(service):
    result = run(service, {
        "start": {"type": "Neighborhood"},
        "traverse": [{"relation": "HAS_SERVICE", "target_type": "ServiceLocation",
                      "as": "services"}],
        "analyses": [{"as": "walk15", "analysis": "accessibility", "mode": "walk",
                      "minutes": 15, "target_category": "grocery"}],
    })
    execution = result["execution"]
    assert execution["start_type"] == "Neighborhood"
    assert execution["relations_traversed"] == ["HAS_SERVICE"]
    assert execution["analyses"][0]["type"] == "accessibility"
    assert execution["analysis_calls"] >= 1
    assert execution["elapsed_seconds"] >= 0

    provenance = result["provenance"]
    assert provenance["datasets"]
    # A query touching only Neighborhood still credits the population dataset,
    # because `children` and friends are denormalized onto that node.
    denormalized = [d for d in provenance["datasets"] if "denormalized" in d["for"]]
    assert denormalized, "the source of the denormalized population fields must be named"
    assert "children" in denormalized[0]["fields"]
    assert provenance["relations_traversed"][0]["persisted"] is True
    assert provenance["analyses"][0]["classification"] == "dynamic"
    assert provenance["origin_method"]
    assert provenance["population_reference_year"]


def test_structural_return_field_links_relation_and_all_matching_sources(service):
    result = run(service, {
        "start": {"type": "Neighborhood"},
        "traverse": [{"relation": "HAS_SERVICE", "target_type": "ServiceLocation",
                      "as": "pharmacies",
                      "filters": [{"field": "category", "op": "eq", "value": "pharmacy"}]}],
        "return": ["Neighborhood.name", "pharmacies.count"],
    })
    field = result["provenance"]["fields"]["results[].pharmacies.count"]
    assert field["classification"] == "derived"
    assert field["relation"] == "HAS_SERVICE"
    assert field["source_refs"]


def test_entity_provenance_classifies_its_source(service):
    area = service.entity("Neighborhood", "area:a")["provenance"]
    assert area["classification"] == "official"
    observation = service.provenance("population:a:2025")
    assert observation["classification"] == "official"
    assert observation["data_mode"] == "fixture"
    assert observation["age_group_definitions"]["children"]
    relation = service.provenance("REACHABLE_WITHIN")
    assert relation["persisted"] is False


# --- the standing questions ---------------------------------------------------
def test_q1_poorest_access(service):
    answer = service.ask("q1_poorest_access", category="grocery")
    assert answer["results"]
    counts = [row["reachable_count"] for row in answer["results"]]
    assert counts == sorted(counts)
    assert "methodology" in answer and answer["methodology"]
    assert answer["provenance"]["origin_method"]


def test_q2_schools_vs_healthcare(service):
    answer = service.ask("q2_schools_vs_healthcare")
    assert "thresholds" in answer
    assert answer["thresholds"]["source"].startswith("medians")
    for row in answer["results"]:
        assert row["school_count"] >= answer["thresholds"]["school_count_at_least"]
        assert row["healthcare_count"] <= answer["thresholds"]["healthcare_count_at_most"]


def test_q2_thresholds_can_be_overridden(service):
    answer = service.ask("q2_schools_vs_healthcare", school_min=0, healthcare_max=999)
    assert answer["results"]
    assert answer["thresholds"]["school_count_at_least"] == 0


def test_q3_adjacent_contrasts(service):
    answer = service.ask("q3_adjacent_contrasts")
    assert answer["total_pairs"] >= 1
    relation = answer["provenance"]["relations_traversed"][0]
    assert relation["relation"] == "ADJACENT_TO"
    assert relation["classification"] == "derived"
    assert relation["persisted"] is True
    for pair in answer["results"]:
        assert pair["difference"] == abs(pair["a_reachable"] - pair["b_reachable"])
        assert pair["largest_category_gap"]["category"]


def test_q4_category_inequality(service):
    answer = service.ask("q4_category_inequality")
    assert answer["measure"].startswith("coefficient of variation")
    values = [row["coefficient_of_variation"] or 0 for row in answer["results"]]
    assert values == sorted(values, reverse=True)
    for row in answer["results"]:
        assert row["max"] >= row["min"]
        assert row["range"] == row["max"] - row["min"]


def test_q5_mode_gain(service):
    answer = service.ask("q5_mode_gain")
    assert answer["compared_modes"]
    for row in answer["results"]:
        for mode, gain in row["gains"].items():
            assert gain["absolute_gain"] == (gain["essential_reachable"]
                                             - row["base_essential_reachable"])


def test_q6_children_underserved(service):
    answer = service.ask("q6_children_underserved")
    thresholds = answer["thresholds"]
    assert thresholds["children_more_than"] is not None
    for row in answer["results"]:
        assert row["children"] > thresholds["children_more_than"]
        assert row["pharmacy_count"] < thresholds["pharmacy_count_below"]
    assert "official Basel-Stadt statistics" in answer["methodology"]
    provenance = answer["provenance"]
    fields = provenance["fields"]
    children = fields["results[].children"]
    assert children["classification"] == "official"
    population = provenance["sources"][children["source_refs"][0]]
    assert population["reference_year"] == 2025
    assert population["age_group_definitions"]["children"] == "aged 0-17 (minors)"
    assert population["data_mode"] == "fixture"

    for field in ("results[].pharmacy_count", "results[].pharmacy_nearest_minutes"):
        computation = provenance["computations"][fields[field]["computation_ref"]]
        assert computation["classification"] == "dynamic"
        assert computation["algorithm"] == "NetworkX single-source Dijkstra"
        assert computation["edge_weight"] == "length_m"
        assert computation["speed_kmh"] > 0
        assert computation["time_budget_minutes"] == answer["minutes"]
        assert computation["source_refs"]

    transit_field = fields["results[].transit_stops_in_walking_range"]
    assert transit_field["classification"] == "dynamic"
    assert "RAPTOR" in provenance["computations"][transit_field["computation_ref"]]["algorithm"]
    for name, operator in (("children_median", "gt"),
                           ("pharmacy_count_below", "lt"),
                           ("transit_stops_below", "lt")):
        derivation = provenance["computations"][
            fields[f"thresholds.{name}"]["computation_ref"]]
        assert derivation["method"] == "median"
        assert derivation["input_count"] == answer["total_neighborhoods"]
        assert derivation["null_semantics"] == "null values ignored"
        assert derivation["comparison_operator"] == operator
    codes = {row["code"] for row in provenance["quality"]["caveats"]}
    assert {"network_not_live", "services_not_live", "transit_not_live"} <= codes
    assert all(row.get("applies_to") for row in provenance["quality"]["caveats"])


def test_q6_structural_transit_fallback_is_explicit():
    from app.modes import TravelMode
    from app.spatial_graph.fixtures import fixture_service

    fallback = fixture_service()
    fallback.analysis.engines.pop(TravelMode.TRANSIT)
    answer = fallback.ask("q6_children_underserved")
    field = answer["provenance"]["fields"]["results[].transit_stops_in_walking_range"]
    assert field["classification"] == "derived"
    assert field["relation"] == "HAS_TRANSIT_STOP"
    assert field["method"] == "point-in-polygon"
    assert "not stops reachable" in field["semantics"]
    assert "not stops reachable" in answer["methodology"]
    assert not any(row["code"].startswith("transit_")
                   for row in answer["provenance"]["quality"]["caveats"])


def test_accessibility_provenance_lists_every_contributing_poi_dataset():
    from dataclasses import replace
    from app.modes import TravelMode
    from app.spatial_graph.fixtures import fixture_service

    service = fixture_service()
    engine = service.analysis.engines[TravelMode.WALK]
    pharmacy = next(item for item in engine.services.services
                    if item.category.value == "pharmacy")
    second = replace(pharmacy, id="service:second-provider", source="second provider",
                     source_dataset="second dataset",
                     source_url="https://example.test/dataset")
    engine.services.services.append(second)
    engine.services.by_category[second.category].append(second)

    provenance = service.ask("q6_children_underserved")["provenance"]
    computation = provenance["computations"]["pharmacy_access"]
    dependencies = {(provenance["sources"][key]["source"],
                     provenance["sources"][key]["dataset"])
                    for key in computation["source_refs"] if key.startswith("service_")}
    assert dependencies == {
        ("synthetic fixture", "Synthetic Basel-centred service fixture"),
        ("second provider", "second dataset"),
    }


def test_missing_quality_metadata_does_not_break_q6():
    from app.spatial_graph.fixtures import fixture_service

    without_quality = fixture_service()
    without_quality.graph.metadata["data_quality"] = {"available": False}
    quality = without_quality.ask("q6_children_underserved")["provenance"]["quality"]
    assert quality == {"available": False, "caveats": []}


def test_every_question_returns_a_methodology(service):
    from app.spatial_graph.questions import QUESTIONS

    for name in QUESTIONS:
        answer = service.ask(name)
        assert answer["methodology"], name
        assert answer["question"], name
        assert answer["provenance"]["graph_mode"] == "fixture", name
        assert answer["provenance"]["computations"], name
        dynamic = [item for item in answer["provenance"]["computations"].values()
                   if item.get("classification") == "dynamic"]
        assert dynamic and all(item.get("algorithm") for item in dynamic), name


def test_an_unknown_question_is_refused(service):
    with pytest.raises(QuerySpecError):
        service.ask("q99_the_meaning_of_life")


# --- example query specifications ---------------------------------------------
def test_the_shipped_examples_are_valid_specifications():
    from pathlib import Path

    examples = sorted(Path("examples/queries").glob("*.json"))
    assert examples, "the repository should ship example queries"
    for path in examples:
        spec = json.loads(path.read_text(encoding="utf-8"))
        QuerySpec.parse(spec)          # raises if the grammar rejects it


# --- API ----------------------------------------------------------------------
def test_schema_endpoints(client):
    schema = client.get("/spatial-graph/schema").json()
    assert "Neighborhood" in schema["entity_types"]
    assert schema["relations"]["LOCATED_IN"]["persisted"] is True
    types = client.get("/spatial-graph/entity-types").json()
    assert {t["name"] for t in types["entity_types"]} == set(NODE_TYPES)
    relations = client.get("/spatial-graph/relation-types").json()
    assert {r["name"] for r in relations["relations"]} == set(RELATION_TYPES)


def test_entity_endpoints(client):
    listing = client.get("/spatial-graph/entities/Neighborhood").json()
    assert listing["type"] == "Neighborhood" and listing["results"]
    assert "geometry" not in listing["results"][0]
    entity_id = listing["results"][0]["id"]
    single = client.get(f"/spatial-graph/entities/Neighborhood/{entity_id}").json()
    assert single["id"] == entity_id
    assert single["provenance"]["classification"] == "official"
    neighbors = client.get(f"/spatial-graph/entities/Neighborhood/{entity_id}/neighbors").json()
    assert neighbors["by_relation"]
    subgraph = client.get(
        f"/spatial-graph/entities/Neighborhood/{entity_id}/subgraph", params={"depth": 2}).json()
    assert subgraph["node_count"] > 1


def test_unknown_entity_type_endpoint(client):
    response = client.get("/spatial-graph/entities/Dragon")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_entity_type"


def test_query_endpoint(client):
    response = client.post("/spatial-graph/query", json={
        "start": {"type": "Neighborhood"},
        "traverse": [{"relation": "HAS_SERVICE", "as": "services"}],
        "return": ["Neighborhood.name", "services.count"],
        "limit": 5,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["results"] and "execution" in body and "provenance" in body
    assert all("geometry" not in json.dumps(row) for row in body["results"])


def test_query_endpoint_rejects_a_bad_specification(client):
    response = client.post("/spatial-graph/query", json={"start": {"type": "Dragon"}})
    assert response.status_code in {404, 422}
    assert response.json()["error"] in {"unknown_entity_type", "invalid_query"}


def test_question_endpoints(client):
    listing = client.get("/spatial-graph/questions").json()
    assert len(listing["questions"]) == 6
    answer = client.get("/spatial-graph/questions/q1_poorest_access",
                        params={"category": "grocery"}).json()
    assert answer["results"] and answer["methodology"]


def test_provenance_endpoint(client):
    body = client.get("/spatial-graph/provenance/LOCATED_IN").json()
    assert body["persisted"] is True
    assert body["classification"] == "derived"


def test_status_endpoint_and_health(client):
    status = client.get("/spatial-graph/status").json()
    assert status["available"] is True
    assert status["node_types"]["Neighborhood"] >= 1
    health = client.get("/health").json()
    assert health["spatial_graph"]["available"] is True
