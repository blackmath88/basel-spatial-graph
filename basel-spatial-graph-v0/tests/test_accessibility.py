"""Walking accessibility: snapping, budgets, traversal, failure modes, API shape."""
import networkx as nx
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString

from app.accessibility import WalkingAccessibilityService
from app.errors import EmptyNetworkError, InvalidCoordinateError, OutsideNetworkError
from app.main import app
from app.street_sources import fixture_street_network
from app.street_sources.base import StreetNetwork, make_provenance


@pytest.fixture
def service(streets, entity_graph):
    return WalkingAccessibilityService(streets, entity_graph)


@pytest.fixture
def client():
    return TestClient(app)


def _line(a, b):
    return LineString([a, b])


def _tiny_network():
    """a-b-c in a line, plus an island d that no edge reaches.

    a --100m-- b --100m-- c        d (isolated)
    """
    graph = nx.Graph()
    coords = {"a": (7.5800, 47.5560), "b": (7.5813304, 47.5560), "c": (7.5826608, 47.5560), "d": (7.5900, 47.5600)}
    for node, (lon, lat) in coords.items():
        graph.add_node(node, lon=lon, lat=lat)
    graph.add_edge("a", "b", length_m=100.0, geom=_line(coords["a"], coords["b"]))
    graph.add_edge("b", "c", length_m=100.0, geom=_line(coords["b"], coords["c"]))
    return StreetNetwork(graph, make_provenance(mode="fixture", source="tiny", dataset="tiny"))


# --- nearest-node snapping ---------------------------------------------------
def test_snaps_to_the_nearest_node(streets):
    node, distance = streets.nearest_node(47.5501, 7.5741)
    assert node == "fixture:0:0"
    assert distance < 20


def test_snap_distance_is_in_metres(streets):
    _, distance = streets.nearest_node(47.5500, 7.5753)  # ~100 m east of the corner
    assert 80 < distance < 120


def test_click_far_outside_the_network_is_an_error(streets):
    with pytest.raises(OutsideNetworkError) as excinfo:
        streets.nearest_node(46.2044, 6.1432)  # Geneva
    assert excinfo.value.status_code == 422
    assert excinfo.value.details["snap_distance_m"] > 100_000


def test_invalid_coordinates_are_rejected(streets):
    with pytest.raises(InvalidCoordinateError):
        streets.nearest_node(47.55, float("nan"))
    with pytest.raises(InvalidCoordinateError):
        streets.nearest_node(1000.0, 7.58)


def test_empty_network_is_reported_not_crashed():
    empty = StreetNetwork(nx.Graph(), make_provenance(mode="fixture", source="t", dataset="t"))
    with pytest.raises(EmptyNetworkError):
        empty.nearest_node(47.55, 7.58)


# --- time budget -------------------------------------------------------------
@pytest.mark.parametrize("minutes,expected_m", [(5, 400), (10, 800), (15, 1200)])
def test_time_budget_converts_to_distance(service, minutes, expected_m):
    result = service.calculate(47.550, 7.574, minutes=minutes, speed_kmh=4.8)
    assert result["network"]["distance_budget_m"] == expected_m
    assert result["network"]["max_network_distance_m"] <= expected_m


def test_walking_speed_is_configurable(service):
    slow = service.calculate(47.550, 7.574, minutes=15, speed_kmh=3.0)
    fast = service.calculate(47.550, 7.574, minutes=15, speed_kmh=6.0)
    assert slow["network"]["distance_budget_m"] == 750
    assert fast["network"]["distance_budget_m"] == 1500
    assert fast["network"]["reachable_node_count"] > slow["network"]["reachable_node_count"]


def test_reachable_set_grows_with_time(service):
    lengths = [
        service.calculate(47.557, 7.582, minutes=m)["network"]["reachable_edge_length_m"]
        for m in (5, 10, 15)
    ]
    assert lengths == sorted(lengths)
    assert lengths[0] < lengths[-1]


def test_zero_or_negative_minutes_is_rejected(service):
    with pytest.raises(InvalidCoordinateError):
        service.calculate(47.550, 7.574, minutes=0)
    with pytest.raises(InvalidCoordinateError):
        service.calculate(47.550, 7.574, minutes=15, speed_kmh=-1)


# --- weighted traversal ------------------------------------------------------
def test_traversal_uses_edge_length_not_hop_count():
    service = WalkingAccessibilityService(_tiny_network())
    # 150 m budget reaches b (100 m) but not c (200 m), despite c being 2 hops.
    result = service.calculate(47.5560, 7.5800, minutes=1.875, speed_kmh=4.8)
    assert result["network"]["distance_budget_m"] == pytest.approx(150.0)
    assert result["network"]["reachable_node_count"] == 2
    assert result["network"]["reachable_edge_count"] == 1
    assert result["network"]["reachable_edge_length_m"] == pytest.approx(100.0)


def test_full_budget_reaches_the_whole_chain():
    service = WalkingAccessibilityService(_tiny_network())
    result = service.calculate(47.5560, 7.5800, minutes=5, speed_kmh=4.8)
    assert result["network"]["reachable_node_count"] == 3
    assert result["network"]["reachable_edge_length_m"] == pytest.approx(200.0)


def test_network_distance_exceeds_straight_line_around_a_barrier(service):
    """The fixture grid has a barrier; reaching past it is a real detour.

    school:2 sits ~1.05 km away in a straight line, but the only crossings are
    on rows 1 and 4, so the walk is far longer than the crow flies.
    """
    result = service.calculate(47.558, 7.586, minutes=25)
    row = next(r for r in result["euclidean_vs_network"] if r["id"] == "school:2")
    assert row["network_distance_m"] > row["euclidean_distance_m"]
    assert row["network_detour_factor"] > 1.5


# --- disconnected fragments and empty results --------------------------------
def test_isolated_node_returns_an_empty_but_valid_result():
    service = WalkingAccessibilityService(_tiny_network())
    result = service.calculate(47.5600, 7.5900, minutes=15)  # snaps to island "d"
    assert result["snapped_origin"]["node_id"] == "d"
    assert result["network"]["reachable_node_count"] == 1
    assert result["network"]["reachable_edge_count"] == 0
    assert result["network"]["reachable_edge_length_m"] == 0
    assert result["geometry"]["features"]  # the straight-line circle is still there
    assert any("isolated" in note or "fragment" in note for note in result["notes"])


def test_disconnected_fragment_size_is_reported():
    service = WalkingAccessibilityService(_tiny_network())
    connected = service.calculate(47.5560, 7.5800, minutes=15)
    island = service.calculate(47.5600, 7.5900, minutes=15)
    assert connected["snapped_origin"]["component_size"] == 3
    assert island["snapped_origin"]["component_size"] == 1
    assert connected["snapped_origin"]["component_index"] != island["snapped_origin"]["component_index"]


def test_nodes_across_a_barrier_stay_unreachable(service):
    """Straight-line-near is not the same as network-reachable.

    From fixture:2:2 the neighbour fixture:3:2 is ~450 m away in a straight
    line but 1.34 km around the barrier, so an 800 m budget must exclude it
    while still including the equally distant fixture:2:3.
    """
    result = service.calculate(47.558, 7.586, minutes=10)
    reachable = set()
    for feature in result["geometry"]["features"]:
        if feature["properties"]["kind"] == "reachable_edge":
            reachable |= {feature["properties"]["source"], feature["properties"]["target"]}
    assert result["snapped_origin"]["node_id"] == "fixture:2:2"
    assert "fixture:2:3" in reachable
    assert "fixture:3:2" not in reachable


# --- entities ----------------------------------------------------------------
def test_reachable_schools_are_sorted_and_within_budget(service):
    result = service.calculate(47.557, 7.582, minutes=15, speed_kmh=4.8)
    distances = [s["network_distance_m"] for s in result["reachable_entities"]["schools"]]
    assert distances and distances == sorted(distances)
    assert max(distances) <= result["network"]["distance_budget_m"]
    assert result["reachable_entities"]["school_count"] == len(distances)


def test_service_works_without_any_entity_graph(streets):
    result = WalkingAccessibilityService(streets).calculate(47.557, 7.582, minutes=10)
    assert result["reachable_entities"]["schools"] == []
    assert result["network"]["reachable_edge_count"] > 0


# --- geometry ----------------------------------------------------------------
def test_geometry_contains_edges_and_the_comparison_circle(service):
    result = service.calculate(47.557, 7.582, minutes=10)
    kinds = [f["properties"]["kind"] for f in result["geometry"]["features"]]
    assert kinds.count("straight_line_radius") == 1
    assert kinds.count("network_buffer") == 0  # opt-in only
    assert kinds.count("reachable_edge") == result["network"]["reachable_edge_count"]


def test_buffer_polygon_is_available_on_request(service):
    result = service.calculate(47.557, 7.582, minutes=10, include_buffer=True)
    buffers = [f for f in result["geometry"]["features"] if f["properties"]["kind"] == "network_buffer"]
    assert len(buffers) == 1
    assert buffers[0]["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert buffers[0]["properties"]["approximate"] is True


def test_straight_line_circle_can_be_switched_off(service):
    result = service.calculate(47.557, 7.582, minutes=10, include_straight_line=False)
    kinds = {f["properties"]["kind"] for f in result["geometry"]["features"]}
    assert "straight_line_radius" not in kinds


def test_geometry_coordinates_stay_in_wgs84(service):
    result = service.calculate(47.557, 7.582, minutes=10)
    for feature in result["geometry"]["features"]:
        if feature["properties"]["kind"] != "reachable_edge":
            continue
        for lon, lat in feature["geometry"]["coordinates"]:
            assert 7.4 < lon < 7.8 and 47.4 < lat < 47.7


# --- API ---------------------------------------------------------------------
def test_walk_api_response_shape(client):
    response = client.get("/accessibility/walk", params={"lat": 47.557, "lon": 7.582, "minutes": 10})
    assert response.status_code == 200
    body = response.json()
    for key in ("origin", "snapped_origin", "minutes", "walking_speed_kmh", "network", "geometry", "reachable_entities", "provenance"):
        assert key in body
    assert set(body["origin"]) == {"lat", "lon"}
    assert {"node_id", "lat", "lon", "snap_distance_m"} <= set(body["snapped_origin"])
    assert {"reachable_node_count", "reachable_edge_count", "reachable_edge_length_m"} <= set(body["network"])
    assert body["geometry"]["type"] == "FeatureCollection"
    assert body["walking_speed_kmh"] == 4.8
    assert body["provenance"]["network_source"]
    assert body["provenance"]["mode"] in {"live", "fixture"}
    assert body["provenance"]["algorithm"] == "NetworkX single-source Dijkstra"


def test_api_reports_a_click_outside_the_network_cleanly(client):
    response = client.get("/accessibility/walk", params={"lat": 46.2044, "lon": 6.1432, "minutes": 15})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "outside_network"
    assert "walkable street" in body["message"]
    assert body["details"]["snap_distance_m"] > 100_000


@pytest.mark.parametrize("params", [
    {"lat": 91, "lon": 7.58},
    {"lat": 47.55, "lon": 200},
    {"lat": 47.55, "lon": 7.58, "minutes": 0},
    {"lat": 47.55, "lon": 7.58, "minutes": 15, "walking_speed_kmh": 0},
])
def test_api_rejects_impossible_parameters(client, params):
    assert client.get("/accessibility/walk", params=params).status_code == 422


def test_health_reports_both_data_modes(client):
    body = client.get("/health").json()
    assert body["streets"]["mode"] == "fixture"
    assert body["entities"]["mode"] == "fixture"
    assert body["streets"]["metric_crs"] == "EPSG:2056"
    assert body["map"]["default_walking_speed_kmh"] == 4.8


def test_entity_accessibility_endpoint(client):
    response = client.get("/entities/schools/school%3A1/accessibility", params={"minutes": 10})
    assert response.status_code == 200
    assert response.json()["network"]["distance_budget_m"] == 800


def test_fixture_network_is_never_labelled_live():
    network = fixture_street_network()
    assert network.provenance["fixture"] is True
    assert network.provenance["mode"] == "fixture"
