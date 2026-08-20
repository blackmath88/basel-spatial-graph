"""The preparation command must never present fixture data as real."""
import networkx as nx
import pytest
from shapely.geometry import LineString

from app import prepare_data as pd
from app.errors import NetworkSourceError
from app.errors import ServiceSourceError
from app.service_model import ServiceCategory
from app.street_sources import OSMnxNetworkSource, fixture_street_network
from app.street_sources.base import LIVE, StreetNetwork, make_provenance, utc_now_iso


def _live_like_network():
    graph = nx.Graph()
    graph.add_node("1", lon=7.5890, lat=47.5560, osmid="1")
    graph.add_node("2", lon=7.5900, lat=47.5560, osmid="2")
    graph.add_edge("1", "2", length_m=75.2, highway="footway", name="Testweg", osmid="42",
                   geom=LineString([(7.5890, 47.5560), (7.5900, 47.5560)]))
    provenance = make_provenance(
        mode=LIVE, source="OpenStreetMap / OSMnx", dataset="OSM walk network",
        license="ODbL 1.0", retrieved_at=utc_now_iso(), place="Basel, Switzerland",
    )
    return StreetNetwork(graph, provenance)


def test_fixture_mode_is_reported_as_fixture(capsys):
    result = pd.prepare_network(fixture=True)
    assert result["status"] == pd.FIXTURE_BANNER
    output = capsys.readouterr().out
    assert "synthetic fixture" in output
    assert "LIVE" not in output


def test_successful_preparation_writes_the_cache(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "net.graphml"
    monkeypatch.setattr(
        pd, "OSMnxNetworkSource",
        lambda **kwargs: OSMnxNetworkSource(**{**kwargs, "cache_path": cache}),
    )
    monkeypatch.setattr(OSMnxNetworkSource, "download", lambda self: _live_like_network())

    result = pd.prepare_network()
    assert result["status"] == pd.LIVE_BANNER
    assert cache.exists()

    output = capsys.readouterr().out
    assert "OpenStreetMap / OSMnx" in output
    assert "nodes:" in output and "edges:" in output
    assert "EPSG:2056" in output
    assert str(cache.name) in output


def test_second_run_reuses_the_cache_instead_of_downloading(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "net.graphml"
    monkeypatch.setattr(
        pd, "OSMnxNetworkSource",
        lambda **kwargs: OSMnxNetworkSource(**{**kwargs, "cache_path": cache}),
    )
    downloads = []

    def fake_download(self):
        downloads.append(1)
        return _live_like_network()

    monkeypatch.setattr(OSMnxNetworkSource, "download", fake_download)
    pd.prepare_network()
    capsys.readouterr()
    result = pd.prepare_network()

    assert len(downloads) == 1
    assert result["status"] == pd.LIVE_BANNER
    assert "reused existing cache" in capsys.readouterr().out


def test_refresh_forces_a_new_download(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "net.graphml"
    monkeypatch.setattr(
        pd, "OSMnxNetworkSource",
        lambda **kwargs: OSMnxNetworkSource(**{**kwargs, "cache_path": cache}),
    )
    downloads = []
    monkeypatch.setattr(OSMnxNetworkSource, "download",
                        lambda self: (downloads.append(1), _live_like_network())[1])
    pd.prepare_network()
    pd.prepare_network(refresh=True)
    capsys.readouterr()
    assert len(downloads) == 2


def test_a_failed_download_falls_back_and_says_so(tmp_path, monkeypatch, capsys):
    class FailingSource:
        def __init__(self, **kwargs):
            self.used_cache = False

        def load(self):
            raise NetworkSourceError("Overpass is unreachable", attempts=["place 'Basel': timeout"])

    monkeypatch.setattr(pd, "OSMnxNetworkSource", FailingSource)
    result = pd.prepare_network()

    assert result["status"] == pd.FIXTURE_BANNER
    assert result["error"] == "Overpass is unreachable"
    output = capsys.readouterr().out
    assert "FAILED" in output
    assert "Overpass is unreachable" in output
    assert "Nothing about Basel geography below is real." in output


def test_missing_osmnx_is_explained_not_traced(monkeypatch):
    monkeypatch.setattr(
        OSMnxNetworkSource, "_import_osmnx",
        staticmethod(lambda: (_ for _ in ()).throw(NetworkSourceError("OSMnx is not installed."))),
    )
    with pytest.raises(NetworkSourceError) as excinfo:
        OSMnxNetworkSource(allow_download=True).download()
    assert "OSMnx is not installed." in excinfo.value.message


def test_cli_exit_code_signals_fixture_mode(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pd, "write_report", lambda report, path=None: tmp_path / "q.json")
    monkeypatch.setattr(pd, "prepare_network",
                        lambda **kw: {"status": pd.FIXTURE_BANNER, "network": fixture_street_network()})
    assert pd.main(["--network-only"]) == 1
    out = capsys.readouterr().out
    assert "FIXTURE" in out
    assert "READY (with fixture fallbacks)" in out


def test_cli_exit_code_signals_live_mode(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pd, "write_report", lambda report, path=None: tmp_path / "q.json")
    monkeypatch.setattr(pd, "prepare_network",
                        lambda **kw: {"status": pd.LIVE_BANNER, "network": _live_like_network()})
    assert pd.main(["--network-only"]) == 0
    out = capsys.readouterr().out
    assert "status  streets:  LIVE" in out
    assert "status  overall:  READY" in out
    assert "uvicorn app.main:app --reload" in out


# --- services ----------------------------------------------------------------
def test_services_are_fetched_snapped_and_cached(monkeypatch, tmp_path, capsys):
    from app import service_sources
    from app.service_sources.fixture_source import FixtureServiceSource

    cache = tmp_path / "services.json"
    monkeypatch.setattr(pd, "SERVICE_CACHE", cache)
    monkeypatch.setattr(service_sources.cache, "SERVICE_CACHE", cache)
    monkeypatch.setattr(service_sources, "_PROVIDERS", {"fixture": FixtureServiceSource})
    monkeypatch.setattr(service_sources, "SOURCE_PLAN", {
        ServiceCategory.GROCERY: ("fixture",), ServiceCategory.PARK: ("fixture",)})

    networks = {"walk": fixture_street_network(), "bike": fixture_street_network(kind="bike")}
    result = pd.prepare_services(networks)

    assert result["status"] == pd.LIVE_BANNER
    assert cache.exists()
    index = result["index"]
    assert index.by_category[ServiceCategory.GROCERY]
    assert all(s.access_node_id for s in index.services if s.is_routable)
    assert all(s.access_for("bike").node_id for s in index.services if s.is_routable_on("bike"))
    out = capsys.readouterr().out
    assert "Service → walk network attachments" in out
    assert "total:" in out


def test_service_preparation_falls_back_and_says_so(monkeypatch, tmp_path, capsys):
    from app import service_sources

    class Failing:
        def fetch(self, category):
            raise ServiceSourceError("all providers down")

    monkeypatch.setattr(pd, "SERVICE_CACHE", tmp_path / "missing.json")
    monkeypatch.setattr(service_sources, "_PROVIDERS", {"bad": Failing})
    monkeypatch.setattr(service_sources, "SOURCE_PLAN", {ServiceCategory.GROCERY: ("bad",)})

    result = pd.prepare_services({"walk": fixture_street_network()})
    assert result["status"] == pd.FIXTURE_BANNER
    out = capsys.readouterr().out
    assert "FAILED" in out and "all providers down" in out


def test_fixture_services_are_reported_as_fixture(capsys):
    result = pd.prepare_services({"walk": fixture_street_network()}, fixture=True)
    assert result["status"] == pd.FIXTURE_BANNER
    assert result["index"].mode == "fixture"
    assert "LIVE" not in capsys.readouterr().out


def test_cached_services_are_reused(monkeypatch, tmp_path, capsys):
    from app import service_sources
    from app.service_index import snap_services
    from app.service_sources import fixture_services, network_fingerprints, write_cache

    cache = tmp_path / "services.json"
    streets = fixture_street_network()
    write_cache(snap_services(streets, fixture_services()),
                network_fingerprints({"walk": streets}), cache)
    monkeypatch.setattr(pd, "SERVICE_CACHE", cache)
    monkeypatch.setattr(service_sources.cache, "SERVICE_CACHE", cache)

    def must_not_run(*a, **kw):
        raise AssertionError("a cached run must not fetch")

    monkeypatch.setattr(pd, "fetch_services", must_not_run)
    result = pd.prepare_services({"walk": streets})
    assert result["status"] == pd.LIVE_BANNER
    assert "reused existing cache" in capsys.readouterr().out


def test_entity_preparation_reports_a_failure_honestly(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pd, "ENTITY_CACHE", tmp_path / "missing.json")
    monkeypatch.setattr(pd, "fetch_entities", lambda: (_ for _ in ()).throw(RuntimeError("HTTP 503")))
    result = pd.prepare_entities()
    assert result["status"] == pd.FIXTURE_BANNER
    assert "HTTP 503" in capsys.readouterr().out
