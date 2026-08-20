"""Source adapters: fixture, cache round-trip, OSMnx conversion, fallback."""
import networkx as nx
import pytest
from shapely.geometry import LineString

from app.errors import NetworkSourceError
from app.projection import to_metric, to_wgs84, validate_lonlat
from app.street_sources import (
    OSMnxWalkingNetworkSource,
    load_street_network,
    read_cache,
    write_cache,
)
from app.street_sources.base import StreetNetwork, make_provenance


# --- coordinate transformation ----------------------------------------------
def test_projection_round_trips_basel():
    x, y = to_metric(7.5895, 47.5570)
    # CH1903+/LV95 places Basel near E 2'611'000 / N 1'267'000.
    assert 2_600_000 < x < 2_625_000
    assert 1_260_000 < y < 1_275_000
    lon, lat = to_wgs84(x, y)
    assert lon == pytest.approx(7.5895, abs=1e-7)
    assert lat == pytest.approx(47.5570, abs=1e-7)


def test_projection_distance_is_metres():
    x1, y1 = to_metric(7.5895, 47.5570)
    x2, y2 = to_metric(7.5895, 47.5660)  # ~1 km due north
    assert 990 < abs(y2 - y1) < 1010
    assert abs(x2 - x1) < 5


@pytest.mark.parametrize("lon,lat", [(200, 47), (7.5, 95), (float("nan"), 47)])
def test_validate_rejects_impossible_coordinates(lon, lat):
    with pytest.raises(ValueError):
        validate_lonlat(lon, lat)


# --- fixture source ----------------------------------------------------------
def test_fixture_network_is_labelled_fixture(streets):
    assert streets.mode == "fixture"
    assert streets.is_live is False
    assert streets.provenance["fixture"] is True
    assert streets.graph.number_of_edges() > 0


def test_fixture_edges_have_metric_lengths(streets):
    lengths = [d["length_m"] for _, _, d in streets.graph.edges(data=True)]
    assert all(l > 0 for l in lengths)
    # The grid spacing is ~440-450 m in both directions.
    assert 400 < min(lengths) < 500


# --- normalization -----------------------------------------------------------
def test_missing_edge_length_is_recovered_from_geometry():
    graph = nx.Graph()
    graph.add_node("a", lon=7.58, lat=47.55)
    graph.add_node("b", lon=7.59, lat=47.55)
    graph.add_edge("a", "b", length_m=None, geom=None)
    network = StreetNetwork(graph, make_provenance(mode="fixture", source="t", dataset="t"))
    assert network.graph["a"]["b"]["length_m"] == pytest.approx(752, abs=25)
    assert network.graph["a"]["b"]["geom"] is not None


def test_zero_length_edges_are_dropped():
    graph = nx.Graph()
    graph.add_node("a", lon=7.58, lat=47.55)
    graph.add_node("b", lon=7.58, lat=47.55)  # identical position
    graph.add_edge("a", "b", length_m="not a number")
    network = StreetNetwork(graph, make_provenance(mode="fixture", source="t", dataset="t"))
    assert network.dropped_edges == 1
    assert network.graph.number_of_edges() == 0


# --- cached-network loading --------------------------------------------------
def test_graphml_cache_round_trip(tmp_path, streets):
    path = write_cache(streets, tmp_path / "net.graphml")
    assert path.exists()
    reloaded = read_cache(path)
    assert reloaded.graph.number_of_nodes() == streets.graph.number_of_nodes()
    assert reloaded.graph.number_of_edges() == streets.graph.number_of_edges()
    assert reloaded.total_length_m() == pytest.approx(streets.total_length_m(), rel=1e-9)
    assert reloaded.provenance["mode"] == "fixture"
    assert reloaded.provenance["cache_path"] == str(path)
    node, distance = reloaded.nearest_node(47.5501, 7.5741)
    assert node == "fixture:0:0"
    assert distance < 20


def test_reading_a_missing_cache_raises(tmp_path):
    with pytest.raises(NetworkSourceError):
        read_cache(tmp_path / "nope.graphml")


def test_reading_a_corrupt_cache_raises(tmp_path):
    path = tmp_path / "broken.graphml"
    path.write_text("<graphml>not really", encoding="utf-8")
    with pytest.raises(NetworkSourceError):
        read_cache(path)


def test_osmnx_source_prefers_the_cache_over_downloading(tmp_path, streets):
    path = write_cache(streets, tmp_path / "net.graphml")
    source = OSMnxWalkingNetworkSource(cache_path=path, allow_download=False)
    network = source.load()
    assert source.used_cache is True
    assert network.graph.number_of_edges() == streets.graph.number_of_edges()


def test_osmnx_source_never_downloads_at_runtime(tmp_path):
    source = OSMnxWalkingNetworkSource(cache_path=tmp_path / "missing.graphml", allow_download=False)
    with pytest.raises(NetworkSourceError) as excinfo:
        source.load()
    assert "prepare_data" in excinfo.value.message


# --- OSMnx conversion (no network access) ------------------------------------
def _osm_like_graph():
    """Mimics what OSMnx returns: MultiDiGraph, both directions, string attrs."""
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=7.5890, y=47.5560)
    graph.add_node(2, x=7.5900, y=47.5560)
    line = LineString([(7.5890, 47.5560), (7.5900, 47.5560)])
    graph.add_edge(1, 2, length="75.2", geometry=line, highway="footway", name="Testweg", osmid=42)
    graph.add_edge(2, 1, length="75.2", geometry=line, highway="footway", name="Testweg", osmid=42)
    graph.add_edge(1, 2, length="180.0", geometry=line.wkt, highway=["service", "footway"], name="Umweg", osmid=[43])
    graph.add_edge(1, 1, length="5.0", geometry=None, highway="footway", name="Loop", osmid=44)
    return graph


def test_osmnx_conversion_collapses_to_shortest_undirected_edge():
    source = OSMnxWalkingNetworkSource(allow_download=False)
    network = source._convert(_osm_like_graph(), place="test", ox_version="2.0.0")
    assert network.graph.number_of_nodes() == 2
    assert network.graph.number_of_edges() == 1  # both directions + parallel + self loop collapse
    edge = network.graph["1"]["2"]
    assert edge["length_m"] == pytest.approx(75.2)
    assert edge["highway"] == "footway"
    assert edge["osmid"] == "42"
    assert network.mode == "live"
    assert network.provenance["license"] == "ODbL 1.0"


def test_osmnx_conversion_rejects_an_empty_result():
    source = OSMnxWalkingNetworkSource(allow_download=False)
    with pytest.raises(NetworkSourceError):
        source._convert(nx.MultiDiGraph(), place="test", ox_version="2.0.0")


# --- resolution / fallback ---------------------------------------------------
def test_load_falls_back_to_fixture_when_no_cache_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEL_STREET_NETWORK_SOURCE", "auto")
    network = load_street_network(cache_path=tmp_path / "missing.graphml")
    assert network.mode == "fixture"
    assert "prepare_data" in network.fallback_reason


def test_explicit_osmnx_mode_refuses_to_pretend(tmp_path, monkeypatch):
    """Fixture data must never be silently presented as the real network."""
    monkeypatch.setenv("BASEL_STREET_NETWORK_SOURCE", "osmnx")
    with pytest.raises(NetworkSourceError):
        load_street_network(cache_path=tmp_path / "missing.graphml")


def test_forced_fixture_mode(tmp_path):
    network = load_street_network(force_fixture=True, cache_path=tmp_path / "missing.graphml")
    assert network.mode == "fixture"
    assert network.fallback_reason == "Fixture mode requested"
