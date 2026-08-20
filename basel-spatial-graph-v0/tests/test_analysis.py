"""City-wide accessibility-gap analysis."""
import networkx as nx
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString

from app.analysis import CityAnalysis, nearest_service_distances
from app.errors import UnknownCategoryError
from app.main import app
from app.service_index import ServiceIndex, snap_services
from app.service_model import ServiceCategory, ServiceLocation
from app.street_sources.base import StreetNetwork, make_provenance


def chain_network(length=6, step_m=100.0):
    """A straight chain a0-a1-...-aN, `step_m` apart."""
    graph = nx.Graph()
    for i in range(length):
        lon = 7.5800 + i * 0.0013304  # ~100 m per step
        graph.add_node(f"n{i}", lon=lon, lat=47.5560)
    for i in range(length - 1):
        a, b = f"n{i}", f"n{i + 1}"
        graph.add_edge(a, b, length_m=step_m, geom=LineString([
            (graph.nodes[a]["lon"], graph.nodes[a]["lat"]),
            (graph.nodes[b]["lon"], graph.nodes[b]["lat"])]))
    return StreetNetwork(graph, make_provenance(mode="fixture", source="chain", dataset="chain"))


def index_with_pharmacy_at_start(network):
    pharmacy = ServiceLocation(id="p0", category=ServiceCategory.PHARMACY,
                               lon=network.graph.nodes["n0"]["lon"], lat=47.5560,
                               source="t", source_dataset="t", source_id="1", name="P")
    snap_services(network, [pharmacy])
    return ServiceIndex([pharmacy])


# --- the multi-source distance map -------------------------------------------
def test_distance_to_nearest_service_grows_along_the_chain():
    network = chain_network()
    distances = nearest_service_distances(network, index_with_pharmacy_at_start(network),
                                          ServiceCategory.PHARMACY)
    assert distances["n0"] == pytest.approx(0, abs=5)
    assert distances["n3"] == pytest.approx(300, abs=5)
    assert distances["n5"] == pytest.approx(500, abs=5)


def test_a_services_snap_distance_seeds_the_search():
    network = chain_network()
    far = ServiceLocation(id="p1", category=ServiceCategory.PHARMACY,
                          lon=network.graph.nodes["n0"]["lon"], lat=47.5569,  # ~100 m off-street
                          source="t", source_dataset="t", source_id="1", name="P")
    snap_services(network, [far])
    distances = nearest_service_distances(network, ServiceIndex([far]), ServiceCategory.PHARMACY)
    assert distances["n0"] == pytest.approx(100, abs=15)


def test_no_services_means_no_coverage():
    network = chain_network()
    assert nearest_service_distances(network, ServiceIndex([]), ServiceCategory.PHARMACY) == {}


def test_unroutable_services_do_not_seed_coverage():
    network = chain_network()
    detached = ServiceLocation(id="p2", category=ServiceCategory.PHARMACY, lon=7.70, lat=47.62,
                               source="t", source_dataset="t", source_id="1", name="P")
    snap_services(network, [detached])
    assert nearest_service_distances(network, ServiceIndex([detached]), ServiceCategory.PHARMACY) == {}


# --- the gap report ----------------------------------------------------------
def test_gaps_split_the_network_at_the_budget():
    network = chain_network()
    analysis = CityAnalysis(network, index_with_pharmacy_at_start(network))
    # 3 minutes at 4.8 km/h = 240 m: nodes n0..n2 are covered, n3..n5 are not.
    report = analysis.accessibility_gaps("pharmacy", minutes=3, speed_kmh=4.8)
    assert report["distance_budget_m"] == pytest.approx(240)
    assert report["network"]["node_count"] == 6
    assert report["network"]["covered_node_count"] == 3
    assert report["network"]["uncovered_node_count"] == 3
    assert report["network"]["covered_ratio"] == pytest.approx(0.5)


def test_a_generous_budget_covers_everything():
    network = chain_network()
    analysis = CityAnalysis(network, index_with_pharmacy_at_start(network))
    report = analysis.accessibility_gaps("pharmacy", minutes=60, speed_kmh=4.8)
    assert report["network"]["uncovered_node_count"] == 0
    assert report["worst_uncovered_points"]["features"] == []


def test_gap_points_are_worst_first_and_spatially_thinned():
    network = chain_network(length=20)
    analysis = CityAnalysis(network, index_with_pharmacy_at_start(network))
    report = analysis.accessibility_gaps("pharmacy", minutes=1, speed_kmh=4.8, limit=5)
    features = report["worst_uncovered_points"]["features"]
    distances = [f["properties"]["distance_to_nearest_m"] for f in features]
    assert distances == sorted(distances, reverse=True)
    assert len(features) <= 5
    # Thinning keeps samples at least 400 m apart, so consecutive nodes are skipped.
    assert distances[0] - distances[1] >= 400


def test_gaps_report_their_own_methodology_and_provenance():
    network = chain_network()
    analysis = CityAnalysis(network, index_with_pharmacy_at_start(network))
    report = analysis.accessibility_gaps("pharmacy", minutes=5, speed_kmh=4.8)
    assert "NOT a population-weighted" in report["method"]
    assert report["provenance"]["classification"] == "exploratory analytical result"
    assert report["provenance"]["algorithm"].startswith("multi-source Dijkstra")
    assert report["prepared_service_count"] == 1


def test_gaps_for_an_unprepared_category():
    network = chain_network()
    analysis = CityAnalysis(network, index_with_pharmacy_at_start(network))
    with pytest.raises(UnknownCategoryError):
        analysis.accessibility_gaps("sport", minutes=15, speed_kmh=4.8)


def test_neighbourhood_coverage_uses_the_entity_polygons(streets, service_index, entity_graph):
    analysis = CityAnalysis(streets, service_index, entity_graph)
    report = analysis.accessibility_gaps("sport", minutes=5, speed_kmh=4.8)
    assert report["neighbourhoods"], "the fixture areas should be reported"
    for row in report["neighbourhoods"]:
        assert 0 <= row["covered_ratio"] <= 1
        assert row["node_count"] > 0
    ratios = [row["covered_ratio"] for row in report["neighbourhoods"]]
    assert ratios == sorted(ratios), "worst-covered neighbourhoods come first"


# --- API ---------------------------------------------------------------------
def test_gaps_endpoint():
    response = TestClient(app).get("/analysis/accessibility-gaps",
                                   params={"category": "pharmacy", "minutes": 5, "limit": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "pharmacy"
    assert body["label"] == "Pharmacies"
    assert body["worst_uncovered_points"]["type"] == "FeatureCollection"
    assert len(body["worst_uncovered_points"]["features"]) <= 3
    assert "method" in body


def test_gaps_endpoint_rejects_an_unknown_category():
    response = TestClient(app).get("/analysis/accessibility-gaps", params={"category": "kebab"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_category"
