"""Walking accessibility: snapping, budgets, traversal, failure modes, API shape."""
import networkx as nx
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString

from app.accessibility import WalkingAccessibilityService
from app.errors import (
    EmptyNetworkError,
    InvalidCoordinateError,
    OutsideNetworkError,
    UnknownServiceError,
    UnroutableServiceError,
)
from app.service_model import ESSENTIAL_CATEGORIES, ServiceCategory
from app.main import app
from app.street_sources import fixture_street_network
from app.street_sources.base import StreetNetwork, make_provenance


@pytest.fixture
def service(streets, entity_graph, service_index):
    return WalkingAccessibilityService(streets, entity_graph, service_index)


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

    The eastern school sits ~1.05 km away in a straight line, but the only
    crossings are on rows 1 and 4, so the walk is far longer than the crow flies.
    """
    result = service.calculate(47.558, 7.586, minutes=25, categories=[ServiceCategory.SCHOOL])
    row = next(r for r in result["euclidean_vs_network"] if r["category"] == "school")
    assert row["network_distance_m"] >= row["euclidean_distance_m"]
    assert row["network_detour_factor"] >= 1.0


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
def test_reachable_services_are_grouped_sorted_and_within_budget(service):
    result = service.calculate(47.557, 7.582, minutes=15, speed_kmh=4.8)
    services = result["reachable_services"]
    assert set(services) >= {c.value for c in ESSENTIAL_CATEGORIES}
    for row in services.values():
        distances = [item["walking_distance_m"] for item in row["items"]]
        assert distances == sorted(distances)
        assert row["count"] == len(row["items"])
        if distances:
            assert max(distances) <= result["network"]["distance_budget_m"]
            assert row["nearest_distance_m"] == distances[0]


def test_schools_are_a_service_category_not_a_special_case(service):
    result = service.calculate(47.557, 7.582, minutes=15)
    assert "schools" not in result["reachable_entities"]
    school = result["reachable_services"]["school"]
    assert school["label"] == "Schools"
    assert school["essential"] is True
    assert school["count"] >= 1


def test_reachable_service_items_carry_provenance(service):
    result = service.calculate(47.557, 7.582, minutes=15)
    item = result["reachable_services"]["grocery"]["items"][0]
    for key in ("id", "name", "display_name", "category", "geometry",
                "walking_distance_m", "walking_time_minutes", "provenance"):
        assert key in item
    assert item["provenance"]["source"]
    assert item["provenance"]["category"] == "grocery"


def test_completeness_is_reported_and_labelled(service):
    result = service.calculate(47.557, 7.582, minutes=15)
    completeness = result["completeness"]
    assert completeness["total"] == len(ESSENTIAL_CATEGORIES)
    assert completeness["label"] == "Prototype accessibility completeness"
    assert 0 <= completeness["reachable_count"] <= completeness["total"]
    reached = {c for c, row in result["reachable_services"].items() if row["count"]}
    assert set(completeness["reachable_categories"]) <= reached


def test_service_counts_grow_with_the_time_budget(service):
    counts = []
    for minutes in (5, 10, 15):
        result = service.calculate(47.5545, 7.5805, minutes=minutes)
        counts.append(sum(row["count"] for row in result["reachable_services"].values()))
    assert counts == sorted(counts)
    assert counts[0] < counts[-1]


def test_categories_can_be_restricted(service):
    result = service.calculate(47.557, 7.582, minutes=15,
                               categories=[ServiceCategory.GROCERY, ServiceCategory.PARK])
    assert set(result["reachable_services"]) == {"grocery", "park"}


def test_services_can_be_switched_off(service):
    result = service.calculate(47.557, 7.582, minutes=15, include_services=False)
    assert result["reachable_services"] == {}
    assert result["completeness"] is None


def test_service_works_without_any_entity_graph_or_services(streets):
    result = WalkingAccessibilityService(streets).calculate(47.557, 7.582, minutes=10)
    assert result["reachable_services"] == {}
    assert result["completeness"]["reachable_count"] == 0
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
    for key in ("origin", "snapped_origin", "minutes", "walking_speed_kmh", "network", "geometry",
                "reachable_services", "completeness", "reachable_entities", "provenance"):
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


# --- service endpoints -------------------------------------------------------
def test_services_summary_endpoint(client):
    body = client.get("/services").json()
    assert body["mode"] == "fixture"
    assert body["total"] > 0
    grocery = next(r for r in body["categories"] if r["category"] == "grocery")
    assert grocery["label"] == "Groceries" and grocery["essential"] is True


def test_services_geojson_endpoint_and_category_filter(client):
    everything = client.get("/services/geojson").json()
    assert everything["type"] == "FeatureCollection"
    parks = client.get("/services/geojson", params={"categories": "park"}).json()
    assert {f["properties"]["category"] for f in parks["features"]} == {"park"}
    assert len(parks["features"]) < len(everything["features"])


def test_services_of_one_category(client):
    body = client.get("/services/pharmacy").json()
    assert {f["properties"]["category"] for f in body["features"]} == {"pharmacy"}


def test_unknown_category_is_a_clean_error(client):
    response = client.get("/services/kebab")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_category"


def test_service_detail_endpoint(client):
    features = client.get("/services/grocery").json()["features"]
    service_id = features[0]["properties"]["id"]
    body = client.get(f"/services/grocery/{service_id}").json()
    assert body["id"] == service_id
    assert body["provenance"]["source"] == "synthetic fixture"
    assert body["access"]["quality"] in {"good", "poor", "unreachable", "unsnapped"}


def test_service_detail_rejects_a_mismatched_category(client):
    features = client.get("/services/grocery").json()["features"]
    response = client.get(f"/services/park/{features[0]['properties']['id']}")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_service"


# --- the profile endpoint ----------------------------------------------------
def test_service_profile_endpoint_skips_geometry(client):
    body = client.get("/accessibility/walk/services",
                      params={"lat": 47.557, "lon": 7.582, "minutes": 15}).json()
    assert "geometry" not in body
    assert body["reachable_services"]["grocery"]["count"] >= 1
    assert body["reachable_services"]["grocery"]["items"] == []
    assert body["completeness"]["total"] == 6


def test_service_profile_can_include_items(client):
    body = client.get("/accessibility/walk/services",
                      params={"lat": 47.557, "lon": 7.582, "minutes": 15, "include_items": True}).json()
    assert body["reachable_services"]["grocery"]["items"]


# --- routing to a service ----------------------------------------------------
def test_route_to_a_service(service):
    result = service.route_to_service(47.5545, 7.5805, "service:pharmacy:fixture:3")
    assert result["service"]["id"] == "service:pharmacy:fixture:3"
    assert result["walking_distance_m"] > 0
    assert result["walking_time_minutes"] > 0
    kinds = [f["properties"]["kind"] for f in result["geometry"]["features"]]
    assert "route_connector" in kinds
    assert result["provenance"]["algorithm"] == "NetworkX Dijkstra shortest path"


def test_route_distance_matches_the_reachability_result(service):
    profile = service.calculate(47.5545, 7.5805, minutes=30)
    nearest = profile["reachable_services"]["grocery"]["items"][0]
    route = service.route_to_service(47.5545, 7.5805, nearest["id"])
    assert route["walking_distance_m"] == pytest.approx(nearest["walking_distance_m"], abs=0.2)


def test_route_to_an_unknown_service(service):
    with pytest.raises(UnknownServiceError):
        service.route_to_service(47.557, 7.582, "service:grocery:nope")


def test_route_to_a_detached_service(streets):
    from app.service_index import ServiceIndex, snap_services
    from app.service_model import ServiceCategory as C
    from app.service_model import ServiceLocation

    detached = ServiceLocation(id="detached", category=C.GROCERY, lon=7.70, lat=47.62,
                               source="t", source_dataset="t", source_id="1", name="Far away")
    snap_services(streets, [detached])
    accessibility = WalkingAccessibilityService(streets, None, ServiceIndex([detached]))
    with pytest.raises(UnroutableServiceError):
        accessibility.route_to_service(47.557, 7.582, "detached")


def test_route_api_endpoint(client):
    response = client.get("/accessibility/walk/route",
                          params={"lat": 47.5545, "lon": 7.5805,
                                  "service_id": "service:pharmacy:fixture:3"})
    assert response.status_code == 200
    assert response.json()["geometry"]["type"] == "FeatureCollection"


def test_route_api_reports_an_unknown_service(client):
    response = client.get("/accessibility/walk/route",
                          params={"lat": 47.557, "lon": 7.582, "service_id": "nope"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_service"


def test_health_reports_the_service_mode(client):
    body = client.get("/health").json()
    assert body["services"]["mode"] == "fixture"
    assert body["services"]["total"] > 0
    assert body["categories"]
    assert all(c["color"].startswith("#") for c in body["categories"])
