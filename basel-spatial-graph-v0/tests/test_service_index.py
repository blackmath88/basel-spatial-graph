"""Snapping services to the walking network, and querying what is reachable."""
import networkx as nx
import pytest
from shapely.geometry import LineString, Polygon

from app.errors import UnknownCategoryError
from app.service_index import ServiceIndex, index_from_payload, snap_services
from app.service_model import ESSENTIAL_CATEGORIES, ServiceCategory, ServiceLocation
from app.service_sources import fixture_services, network_fingerprints
from app.street_sources.base import StreetNetwork, make_provenance

SPEED = 4.8
MINUTE_M = SPEED * 1000 / 60  # 80 m per minute


def tiny_network():
    """a --100m-- b --100m-- c, plus an isolated node d."""
    graph = nx.Graph()
    coords = {"a": (7.5800, 47.5560), "b": (7.5813304, 47.5560),
              "c": (7.5826608, 47.5560), "d": (7.5900, 47.5600)}
    for node, (lon, lat) in coords.items():
        graph.add_node(node, lon=lon, lat=lat)
    graph.add_edge("a", "b", length_m=100.0, geom=LineString([coords["a"], coords["b"]]))
    graph.add_edge("b", "c", length_m=100.0, geom=LineString([coords["b"], coords["c"]]))
    return StreetNetwork(graph, make_provenance(mode="fixture", source="tiny", dataset="tiny"))


def service(category, lon, lat, sid, name="x", **kwargs):
    return ServiceLocation(id=sid, category=ServiceCategory.parse(category), lon=lon, lat=lat,
                           name=name, source="test", source_dataset="test", source_id=sid, **kwargs)


# --- snapping ----------------------------------------------------------------
def test_services_snap_to_the_nearest_node_with_a_distance():
    network = tiny_network()
    services = [service("grocery", 7.5800, 47.5560, "s1")]
    snap_services(network, services)
    assert services[0].access_node_id == "a"
    assert services[0].access_distance_m < 5
    assert services[0].access_quality == "good"


def test_snap_quality_flags_distant_services():
    network = tiny_network()
    close = service("grocery", 7.58005, 47.5560, "close")
    far = service("grocery", 7.5800, 47.5578, "far")          # ~200 m north
    hopeless = service("grocery", 7.5800, 47.5620, "hopeless")  # ~670 m north
    snap_services(network, [close, far, hopeless], poor_m=150, max_m=500)

    assert close.access_quality == "good" and close.is_routable
    assert far.access_quality == "poor" and far.is_routable   # flagged, still usable
    assert hopeless.access_quality == "unreachable"
    assert hopeless.access_node_id is None and not hopeless.is_routable


def test_area_services_snap_from_their_outline_not_their_centre():
    """A park's centre can be far from any street while its edge is on one."""
    network = tiny_network()
    # A park whose centre is ~250 m north of the street but whose southern edge
    # touches node "b".
    park = Polygon([(7.5810, 47.5561), (7.5817, 47.5561), (7.5817, 47.5605), (7.5810, 47.5605)])
    centre = park.representative_point()
    with_outline = service("park", centre.x, centre.y, "with", footprint_wkt=park.wkt)
    without_outline = service("park", centre.x, centre.y, "without")
    snap_services(network, [with_outline, without_outline])

    assert with_outline.access_distance_m < without_outline.access_distance_m
    assert with_outline.access_distance_m < 30
    assert with_outline.access_quality == "good"
    assert without_outline.access_quality in {"poor", "unreachable"}


def test_a_broken_outline_falls_back_to_the_point():
    network = tiny_network()
    broken = service("park", 7.5800, 47.5560, "broken", footprint_wkt="NOT WKT")
    snap_services(network, [broken])
    assert broken.access_node_id == "a"


def test_snapping_an_empty_list_is_harmless():
    assert snap_services(tiny_network(), []) == []


# --- index construction ------------------------------------------------------
def test_only_routable_services_enter_the_access_map():
    network = tiny_network()
    good = service("grocery", 7.5800, 47.5560, "good")
    detached = service("grocery", 7.5800, 47.5700, "detached")
    snap_services(network, [good, detached])
    index = ServiceIndex([good, detached])

    assert index.by_category[ServiceCategory.GROCERY] == [good, detached]
    assert index.access_map["a"] == [good]
    assert all(detached not in bucket for bucket in index.access_map.values())


def test_unknown_category_lookup_lists_what_is_available(service_index):
    with pytest.raises(UnknownCategoryError) as excinfo:
        service_index.of_category("kebab")
    assert excinfo.value.status_code in {404, 422}


def test_summary_reports_counts_sources_and_essentials(service_index):
    summary = service_index.summary()
    assert summary["total"] == len(service_index.services)
    assert summary["essential_categories"] == [c.value for c in ESSENTIAL_CATEGORIES]
    grocery = next(r for r in summary["categories"] if r["category"] == "grocery")
    assert grocery["count"] == 3
    assert grocery["essential"] is True
    assert grocery["sources"] == ["synthetic fixture"]
    assert grocery["color"].startswith("#")


def test_feature_collection_can_be_filtered(service_index):
    everything = service_index.feature_collection()
    parks = service_index.feature_collection([ServiceCategory.PARK])
    assert len(everything["features"]) == len(service_index.services)
    assert {f["properties"]["category"] for f in parks["features"]} == {"park"}
    assert parks["features"][0]["geometry"]["type"] == "Point"


# --- reachability ------------------------------------------------------------
def _index_on_tiny():
    network = tiny_network()
    services = [
        service("grocery", 7.5800, 47.5560, "g-at-a"),      # 0 m from a
        service("grocery", 7.5826608, 47.5560, "g-at-c"),   # 0 m from c (200 m walk)
        service("pharmacy", 7.5813304, 47.5560, "p-at-b"),  # 100 m walk
        service("park", 7.5900, 47.5600, "park-on-island"), # attached to isolated d
    ]
    snap_services(network, services)
    return network, ServiceIndex(services)


def test_reachable_respects_the_distance_budget():
    network, index = _index_on_tiny()
    costs = nx.single_source_dijkstra_path_length(network.graph, "a", cutoff=150, weight="length_m")
    result = index.reachable(costs, 150, SPEED)
    assert result["grocery"]["count"] == 1          # the one at c is 200 m away
    assert result["pharmacy"]["count"] == 1
    assert result["park"]["count"] == 0             # different network component


def test_reachable_items_are_sorted_by_travel_time():
    network, index = _index_on_tiny()
    costs = nx.single_source_dijkstra_path_length(network.graph, "a", cutoff=400, weight="length_m")
    items = index.reachable(costs, 400, SPEED)["grocery"]["items"]
    times = [i["walking_time_minutes"] for i in items]
    assert times == sorted(times)
    assert [i["id"] for i in items] == ["g-at-a", "g-at-c"]


def test_walking_time_uses_the_configured_speed():
    network, index = _index_on_tiny()
    costs = nx.single_source_dijkstra_path_length(network.graph, "a", cutoff=400, weight="length_m")
    slow = index.reachable(costs, 400, 2.4)["pharmacy"]["items"][0]
    fast = index.reachable(costs, 400, 4.8)["pharmacy"]["items"][0]
    assert slow["walking_distance_m"] == fast["walking_distance_m"]
    assert slow["walking_time_minutes"] == pytest.approx(2 * fast["walking_time_minutes"], abs=0.2)


def test_nearest_is_reported_per_category():
    network, index = _index_on_tiny()
    costs = nx.single_source_dijkstra_path_length(network.graph, "a", cutoff=400, weight="length_m")
    grocery = index.reachable(costs, 400, SPEED)["grocery"]
    assert grocery["nearest_id"] == "g-at-a"
    assert grocery["nearest_distance_m"] < 5
    assert grocery["nearest_minutes"] == 0.0
    assert grocery["prepared_total"] == 2


def test_a_service_snap_distance_counts_towards_the_walk():
    network = tiny_network()
    off_street = service("grocery", 7.5800, 47.5569, "off")  # ~100 m from node a
    snap_services(network, [off_street])
    index = ServiceIndex([off_street])
    costs = {"a": 0.0}
    assert index.reachable(costs, 50, SPEED)["grocery"]["count"] == 0
    assert index.reachable(costs, 150, SPEED)["grocery"]["count"] == 1


def test_categories_can_be_filtered():
    network, index = _index_on_tiny()
    costs = nx.single_source_dijkstra_path_length(network.graph, "a", cutoff=400, weight="length_m")
    result = index.reachable(costs, 400, SPEED, categories=[ServiceCategory.PHARMACY])
    assert set(result) == {"pharmacy"}


def test_items_can_be_limited_and_report_truncation():
    network, index = _index_on_tiny()
    costs = nx.single_source_dijkstra_path_length(network.graph, "a", cutoff=400, weight="length_m")
    grocery = index.reachable(costs, 400, SPEED, limit=1)["grocery"]
    assert grocery["count"] == 2 and len(grocery["items"]) == 1
    assert grocery["truncated"] is True


def test_counts_without_items():
    network, index = _index_on_tiny()
    costs = nx.single_source_dijkstra_path_length(network.graph, "a", cutoff=400, weight="length_m")
    grocery = index.reachable(costs, 400, SPEED, include_items=False)["grocery"]
    assert grocery["count"] == 2 and grocery["items"] == []


# --- completeness ------------------------------------------------------------
def test_completeness_counts_categories_not_services():
    reachable = {c.value: {"count": 3} for c in ESSENTIAL_CATEGORIES}
    result = ServiceIndex.completeness(reachable)
    assert result["reachable_count"] == 6 and result["total"] == 6
    assert result["ratio"] == 1.0 and result["missing_categories"] == []


def test_completeness_reports_the_missing_categories():
    reachable = {c.value: {"count": 1} for c in ESSENTIAL_CATEGORIES}
    reachable["sport"] = {"count": 0}
    reachable.pop("park")
    result = ServiceIndex.completeness(reachable)
    assert result["reachable_count"] == 4
    assert sorted(result["missing_categories"]) == ["park", "sport"]


def test_completeness_is_labelled_as_a_prototype():
    result = ServiceIndex.completeness({})
    assert result["label"] == "Prototype accessibility completeness"
    assert "not an official" in result["definition"]
    assert result["reachable_count"] == 0 and result["ratio"] == 0.0


def test_optional_categories_never_affect_completeness():
    reachable = {c.value: {"count": 1} for c in ESSENTIAL_CATEGORIES}
    reachable["library"] = {"count": 99}
    assert ServiceIndex.completeness(reachable)["total"] == 6


# --- cache-backed construction -----------------------------------------------
def test_index_reuses_cached_snapping_when_the_networks_match(streets, bike_network):
    networks = {"walk": streets, "bike": bike_network}
    services = fixture_services()
    for name, network in networks.items():
        snap_services(network, services, network=name)
    payload = {"services": services, "mode": "live",
               "network_fingerprints": network_fingerprints(networks)}
    index = index_from_payload(payload, networks)
    assert index.resnapped == ()
    assert index.mode == "live"


def test_index_resnaps_only_the_network_that_changed(streets, bike_network):
    networks = {"walk": streets, "bike": bike_network}
    services = fixture_services()
    snap_services(streets, services, network="walk")
    payload = {"services": services, "mode": "live",
               "network_fingerprints": {"walk": network_fingerprints(networks)["walk"],
                                        "bike": "stale"}}
    index = index_from_payload(payload, networks)
    assert index.resnapped == ("bike",)
    assert all(s.access_for("bike").node_id for s in index.services if s.is_routable_on("bike"))


def test_a_v03_cache_resnaps_the_new_bike_network(streets, bike_network):
    """The old cache never saw a bicycle network, so that one must be built."""
    networks = {"walk": streets, "bike": bike_network}
    services = fixture_services()
    snap_services(streets, services, network="walk")
    payload = {"services": services, "mode": "live",
               "network_fingerprint": network_fingerprints(networks)["walk"]}
    index = index_from_payload(payload, networks)
    assert index.resnapped == ("bike",)
