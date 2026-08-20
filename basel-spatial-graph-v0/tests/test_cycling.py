"""Cycling: its own network, its own speed, its own access points."""
import pytest
from fastapi.testclient import TestClient

from app.accessibility import CyclingAccessibilityService, WalkingAccessibilityService
from app.config import DEFAULT_CYCLING_SPEED_KMH
from app.errors import UnroutableServiceError
from app.main import app
from app.modes import NETWORK_FOR_MODE, TravelMode
from app.service_index import ServiceIndex, snap_services
from app.service_model import ServiceCategory, ServiceLocation
from app.street_sources import (
    OSMnxCyclingNetworkSource,
    load_network,
    read_cache,
    write_cache,
)


@pytest.fixture
def cycling(bike_network, entity_graph, service_index):
    return CyclingAccessibilityService(bike_network, entity_graph, service_index)


@pytest.fixture
def walking(streets, entity_graph, service_index):
    return WalkingAccessibilityService(streets, entity_graph, service_index)


@pytest.fixture
def client():
    return TestClient(app)


# --- the network is genuinely a different graph -------------------------------
def test_the_bike_network_is_not_the_pedestrian_one(streets, bike_network):
    assert bike_network.kind == "bike"
    assert streets.kind == "walk"
    assert set(bike_network.graph.nodes).isdisjoint(streets.graph.nodes)
    # The fixture bicycle grid crosses the barrier the footpath network lacks.
    assert bike_network.graph.number_of_edges() > streets.graph.number_of_edges()


def test_the_bike_source_targets_the_bicycle_filter():
    source = OSMnxCyclingNetworkSource(allow_download=False)
    assert source.kind == "bike"
    assert source.network_type == "bike"
    assert "cycling" in str(source.cache_path).lower()


def test_modes_map_to_their_networks():
    assert NETWORK_FOR_MODE[TravelMode.BIKE] == "bike"
    assert NETWORK_FOR_MODE[TravelMode.WALK] == "walk"
    assert NETWORK_FOR_MODE[TravelMode.TRANSIT] == "walk"


# --- snapping -----------------------------------------------------------------
def test_snaps_to_the_nearest_bicycle_node(bike_network):
    node, distance = bike_network.nearest_node(47.5501, 7.5741)
    assert node == "bike:0:0"
    assert distance < 20


def test_services_carry_a_separate_bike_access_point(service_index):
    service = service_index.services[0]
    assert service.access_for("walk").node_id.startswith("fixture:")
    assert service.access_for("bike").node_id.startswith("bike:")


def test_a_service_can_be_attached_to_one_network_only(streets, bike_network):
    service = ServiceLocation(id="x", category=ServiceCategory.GROCERY, lon=7.5745, lat=47.5505,
                              source="t", source_dataset="t", source_id="1", name="Shop")
    snap_services(streets, [service], network="walk")
    assert service.is_routable_on("walk")
    assert not service.is_routable_on("bike")
    snap_services(bike_network, [service], network="bike")
    assert service.is_routable_on("bike")


# --- the cost model -----------------------------------------------------------
@pytest.mark.parametrize("minutes,expected_m", [(5, 1250), (10, 2500), (15, 3750)])
def test_time_budget_uses_the_cycling_speed(cycling, minutes, expected_m):
    result = cycling.calculate(47.550, 7.574, minutes=minutes)
    assert result["speed_kmh"] == DEFAULT_CYCLING_SPEED_KMH == 15.0
    assert result["network"]["distance_budget_m"] == expected_m
    assert result["network"]["max_network_distance_m"] <= expected_m


def test_cycling_speed_is_configurable(cycling):
    slow = cycling.calculate(47.550, 7.574, minutes=10, speed_kmh=10)
    fast = cycling.calculate(47.550, 7.574, minutes=10, speed_kmh=25)
    assert slow["network"]["distance_budget_m"] == pytest.approx(1666.7, abs=1)
    assert fast["network"]["distance_budget_m"] == pytest.approx(4166.7, abs=1)
    assert fast["network"]["reachable_node_count"] >= slow["network"]["reachable_node_count"]


def test_the_result_is_tagged_as_cycling(cycling):
    result = cycling.calculate(47.550, 7.574, minutes=15)
    assert result["mode"] == "bike"
    assert result["mode_label"] == "Cycling"
    assert result["provenance"]["travel_mode"] == "bike"
    assert result["provenance"]["network_kind"] == "bike"
    assert "15 km/h" in result["provenance"]["routing_method"]


# --- reach --------------------------------------------------------------------
def test_cycling_reaches_further_than_walking(cycling, walking):
    origin = (47.5545, 7.5805)
    bike = cycling.calculate(*origin, minutes=15)
    walk = walking.calculate(*origin, minutes=15)
    assert bike["network"]["reachable_edge_length_m"] > walk["network"]["reachable_edge_length_m"]
    assert (sum(r["count"] for r in bike["reachable_services"].values())
            > sum(r["count"] for r in walk["reachable_services"].values()))


def test_cycling_uses_the_bike_access_points(cycling):
    result = cycling.calculate(47.5545, 7.5805, minutes=15)
    for row in result["reachable_services"].values():
        for item in row["items"]:
            assert item["access_network"] == "bike"
            assert item["access"]["node_id"].startswith("bike:")


def test_reachable_services_are_sorted_by_travel_time(cycling):
    result = cycling.calculate(47.5545, 7.5805, minutes=15)
    for row in result["reachable_services"].values():
        times = [item["travel_time_minutes"] for item in row["items"]]
        assert times == sorted(times)


def test_completeness_is_computed_per_mode(cycling, walking):
    origin = (47.5545, 7.5805)
    assert (cycling.calculate(*origin, minutes=15)["completeness"]["reachable_count"]
            >= walking.calculate(*origin, minutes=15)["completeness"]["reachable_count"])


# --- routing ------------------------------------------------------------------
def test_shortest_bike_path_to_a_service(cycling):
    result = cycling.route_to_service(47.5545, 7.5805, "service:grocery:fixture:1")
    assert result["mode"] == "bike"
    assert result["walking_distance_m"] > 0
    assert result["node_count"] >= 2
    assert result["provenance"]["network_kind"] == "bike"


def test_bike_route_distance_matches_the_profile(cycling):
    profile = cycling.calculate(47.5545, 7.5805, minutes=30)
    nearest = profile["reachable_services"]["grocery"]["items"][0]
    route = cycling.route_to_service(47.5545, 7.5805, nearest["id"])
    assert route["walking_distance_m"] == pytest.approx(nearest["travel_distance_m"], abs=0.2)


def test_a_service_not_attached_to_the_bike_network_is_refused(bike_network):
    detached = ServiceLocation(id="detached", category=ServiceCategory.GROCERY, lon=7.70, lat=47.62,
                               source="t", source_dataset="t", source_id="1", name="Far away")
    snap_services(bike_network, [detached], network="bike")
    service = CyclingAccessibilityService(bike_network, None, ServiceIndex([detached], networks=("bike",)))
    with pytest.raises(UnroutableServiceError) as excinfo:
        service.route_to_service(47.557, 7.582, "detached")
    assert excinfo.value.details["network"] == "bike"


# --- cache --------------------------------------------------------------------
def test_the_bike_network_round_trips_through_its_cache(tmp_path, bike_network):
    path = write_cache(bike_network, tmp_path / "bike.graphml")
    reloaded = read_cache(path)
    assert reloaded.kind == "bike"
    assert reloaded.graph.number_of_edges() == bike_network.graph.number_of_edges()
    assert reloaded.total_length_m() == pytest.approx(bike_network.total_length_m(), rel=1e-9)


def test_a_missing_bike_cache_degrades_to_the_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEL_STREET_NETWORK_SOURCE", "auto")
    network = load_network("bike", cache_path=tmp_path / "missing.graphml")
    assert network.mode == "fixture"
    assert network.kind == "bike"
    assert "prepare_data" in network.fallback_reason


# --- API ----------------------------------------------------------------------
def test_bike_endpoint_schema(client):
    body = client.get("/accessibility/bike",
                      params={"lat": 47.5545, "lon": 7.5805, "minutes": 15}).json()
    for key in ("origin", "snapped_origin", "minutes", "speed_kmh", "network", "geometry",
                "reachable_services", "completeness", "provenance"):
        assert key in body
    assert body["mode"] == "bike"
    assert body["speed_kmh"] == 15.0
    assert body["provenance"]["network_kind"] == "bike"


def test_unified_endpoint_serves_every_mode(client):
    for mode in ("walk", "bike", "transit"):
        response = client.get("/accessibility",
                              params={"lat": 47.5545, "lon": 7.5805, "mode": mode, "minutes": 15})
        assert response.status_code == 200, mode
        assert response.json()["mode"] == mode


def test_unknown_mode_is_a_clean_error(client):
    response = client.get("/accessibility",
                          params={"lat": 47.5545, "lon": 7.5805, "mode": "teleport"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_mode"


def test_bike_route_endpoint(client):
    response = client.get("/accessibility/bike/route",
                          params={"lat": 47.5545, "lon": 7.5805,
                                  "service_id": "service:grocery:fixture:1"})
    assert response.status_code == 200
    assert response.json()["mode"] == "bike"


def test_health_reports_the_bike_network(client):
    body = client.get("/health").json()
    assert body["bike"]["mode"] == "fixture"
    assert body["bike"]["nodes"] > 0
    assert {m["mode"] for m in body["modes"]} == {"walk", "bike", "transit"}


def test_walking_endpoint_is_unchanged(client):
    """V0.3 clients must keep working."""
    body = client.get("/accessibility/walk",
                      params={"lat": 47.557, "lon": 7.582, "minutes": 10}).json()
    assert body["mode"] == "walk"
    assert body["walking_speed_kmh"] == 4.8
    assert body["network"]["distance_budget_m"] == 800
    assert "reachable_services" in body and "completeness" in body
