"""Service providers: normalization, provenance, merging, caching, fallback."""
import json

import pytest

from app import service_sources
from app.errors import ServiceSourceError
from app.service_model import ServiceCategory, ServiceLocation
from app.service_sources import (
    SOURCE_PLAN,
    fetch_services,
    fixture_services,
    load_services,
    network_fingerprint,
    read_cache,
    write_cache,
)
from app.service_sources.base import duplicate_candidates, normalize_name, safe_id
from app.service_sources.basel_open_data import BaselOpenDataServiceSource
from app.service_sources.fixture_source import FixtureServiceSource


# --- helpers -----------------------------------------------------------------
def test_ids_are_url_safe():
    assert safe_id("service", "park", "osm", "way/123") == "service:park:osm:way-123"
    assert "/" not in safe_id("a", "b/c d")


@pytest.mark.parametrize("raw,expected", [
    ("  Coop   Basel ", "Coop Basel"), ("", None), (None, None), ("nan", None), ("-", None),
])
def test_names_are_normalized_never_invented(raw, expected):
    assert normalize_name(raw) == expected


def test_duplicate_candidates_flags_close_pairs_with_compatible_names():
    def at(lon, lat, name, sid):
        return ServiceLocation(id=sid, category=ServiceCategory.PHARMACY, lon=lon, lat=lat,
                               source="t", source_dataset="t", source_id=sid, name=name)
    same = duplicate_candidates([at(7.59, 47.55, "Apotheke", "a"), at(7.590001, 47.550001, "Apotheke", "b")])
    assert len(same) == 1 and same[0]["distance_m"] < 1
    different = duplicate_candidates([at(7.59, 47.55, "Apotheke A", "a"), at(7.590001, 47.550001, "Apotheke B", "b")])
    assert different == []
    far = duplicate_candidates([at(7.59, 47.55, "Apotheke", "a"), at(7.60, 47.56, "Apotheke", "b")])
    assert far == []


# --- the source plan ---------------------------------------------------------
def test_every_essential_category_has_a_configured_provider():
    from app.service_model import ESSENTIAL_CATEGORIES

    for category in ESSENTIAL_CATEGORIES:
        assert SOURCE_PLAN.get(category), f"{category.value} has no provider"


def test_a_category_may_merge_several_providers():
    assert SOURCE_PLAN[ServiceCategory.HEALTHCARE] == ("bs", "osm")


# --- fixture provider --------------------------------------------------------
def test_fixture_provider_is_deterministic():
    first, second = fixture_services(), fixture_services()
    assert [s.id for s in first] == [s.id for s in second]
    assert all(s.source == "synthetic fixture" for s in first)
    assert all(s.retrieved_at == first[0].retrieved_at for s in first)


def test_fixture_provider_covers_every_essential_category():
    from app.service_model import ESSENTIAL_CATEGORIES

    present = {s.category for s in fixture_services()}
    assert set(ESSENTIAL_CATEGORIES) <= present


def test_fixture_provider_serves_one_category_at_a_time():
    parks = FixtureServiceSource().fetch(ServiceCategory.PARK)
    assert parks and all(s.category is ServiceCategory.PARK for s in parks)
    assert parks[0].name is None  # the fixture park is deliberately unnamed


# --- Basel Open Data provider ------------------------------------------------
SPORT_ROW = {
    "id": 295, "name": "Gartenbad Bachgraben", "kategorie": "Gartenbad",
    "strasse": "Belforterstrasse 135", "zustaendigkeit": "Sportamt BS",
    "geo_point_2d": {"lon": 7.558047, "lat": 47.56349},
}


def test_basel_open_data_normalizes_with_provenance(monkeypatch):
    monkeypatch.setattr("app.service_sources.basel_open_data.fetch_dataset",
                        lambda *a, **kw: [SPORT_ROW])
    service = BaselOpenDataServiceSource().fetch(ServiceCategory.SPORT)[0]
    assert service.category is ServiceCategory.SPORT
    assert service.name == "Gartenbad Bachgraben"
    assert service.id == "service:sport:bs:100151:295"
    assert service.source_id == "295"
    assert service.source_dataset.startswith("100151")
    assert service.license == "CC BY 3.0 CH"
    assert service.retrieved_at
    assert service.attributes["kategorie"] == "Gartenbad"
    assert (service.lon, service.lat) == (7.558047, 47.56349)


def test_basel_open_data_derives_stable_ids_for_keyless_datasets(monkeypatch):
    row = {"sc_schulstandort": "TS Rittergasse", "geo_point_2d": {"lon": 7.5923, "lat": 47.5558}}
    monkeypatch.setattr("app.service_sources.basel_open_data.fetch_dataset",
                        lambda *a, **kw: [row, dict(row)])
    services = BaselOpenDataServiceSource().fetch(ServiceCategory.SCHOOL)
    assert services[0].id == services[1].id  # position, not row order, decides


def test_basel_open_data_reports_an_unavailable_dataset(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr("app.service_sources.basel_open_data.fetch_dataset", boom)
    with pytest.raises(ServiceSourceError) as excinfo:
        BaselOpenDataServiceSource().fetch(ServiceCategory.SPORT)
    assert "HTTP 503" in excinfo.value.message


def test_basel_open_data_refuses_a_category_it_does_not_serve():
    with pytest.raises(ServiceSourceError):
        BaselOpenDataServiceSource().fetch(ServiceCategory.GROCERY)


# --- OpenStreetMap provider (no network) -------------------------------------
def _osm_frame():
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Point, Polygon

    big = Polygon([(7.590, 47.556), (7.5915, 47.556), (7.5915, 47.5570), (7.590, 47.5570)])
    tiny = Polygon([(7.594, 47.556), (7.59404, 47.556), (7.59404, 47.55602), (7.594, 47.55602)])
    private = Polygon([(7.596, 47.556), (7.5975, 47.556), (7.5975, 47.5570), (7.596, 47.5570)])
    frame = geopandas.GeoDataFrame(
        {
            "name": ["Big Park", None, "Private Park", "Point Park"],
            "leisure": ["park", "park", "park", "park"],
            "access": [None, None, "private", None],
            "geometry": [big, tiny, private, Point(7.599, 47.556)],
        },
        crs="EPSG:4326",
    )
    frame.index = geopandas.pd.MultiIndex.from_tuples(
        [("way", 1), ("way", 2), ("way", 3), ("node", 4)], names=["element", "id"])
    return frame


def test_osm_source_converts_features_with_provenance(monkeypatch):
    from app.service_sources.osm_source import OSMServiceSource

    frame = _osm_frame()
    monkeypatch.setattr(OSMServiceSource, "_import_osmnx", staticmethod(lambda: _FakeOx(frame)))
    services = OSMServiceSource().fetch(ServiceCategory.PARK)

    # Only the large public polygon survives: tiny green strip, private site and
    # the point (an area category needs an area) are all filtered out.
    assert [s.name for s in services] == ["Big Park"]
    service = services[0]
    assert service.id == "service:park:osm:way:1"
    assert service.source_id == "way/1"
    assert service.source_url == "https://www.openstreetmap.org/way/1"
    assert service.license == "ODbL 1.0"
    assert service.attributes["leisure"] == "park"
    assert service.attributes["area_m2"] > 500
    assert service.footprint_wkt and service.footprint_wkt.startswith("POLYGON")
    # The representative point must lie inside the park.
    assert 7.590 <= service.lon <= 7.5915 and 47.556 <= service.lat <= 47.5570


def test_osm_source_reports_when_nothing_comes_back(monkeypatch):
    from app.service_sources.osm_source import OSMServiceSource

    geopandas = pytest.importorskip("geopandas")
    empty = geopandas.GeoDataFrame({"geometry": []}, crs="EPSG:4326")
    empty.index = geopandas.pd.MultiIndex.from_tuples([], names=["element", "id"])
    monkeypatch.setattr(OSMServiceSource, "_import_osmnx", staticmethod(lambda: _FakeOx(empty)))
    with pytest.raises(ServiceSourceError):
        OSMServiceSource().fetch(ServiceCategory.PARK)


def test_osm_source_explains_a_missing_dependency(monkeypatch):
    from app.service_sources.osm_source import OSMServiceSource

    def missing():
        raise ServiceSourceError("OSMnx is not installed.")

    monkeypatch.setattr(OSMServiceSource, "_import_osmnx", staticmethod(missing))
    with pytest.raises(ServiceSourceError) as excinfo:
        OSMServiceSource().fetch(ServiceCategory.GROCERY)
    assert "OSMnx is not installed." in excinfo.value.message


class _FakeOx:
    """Stands in for the osmnx module; never touches the network."""

    __version__ = "2.0.0-test"

    def __init__(self, frame):
        self._frame = frame
        self.settings = type("S", (), {"use_cache": True, "cache_folder": "", "log_console": False})()

    def features_from_place(self, place, tags):
        return self._frame


# --- merging and error isolation ---------------------------------------------
def test_fetch_services_isolates_a_failing_provider(monkeypatch):
    class Good:
        def fetch(self, category):
            return FixtureServiceSource().fetch(category)

    class Bad:
        def fetch(self, category):
            raise ServiceSourceError("provider down")

    monkeypatch.setattr(service_sources, "_PROVIDERS", {"good": Good, "bad": Bad})
    monkeypatch.setattr(service_sources, "SOURCE_PLAN", {
        ServiceCategory.GROCERY: ("good",),
        ServiceCategory.PHARMACY: ("bad",),
        ServiceCategory.PARK: ("good", "bad"),
    })
    services, errors = fetch_services(
        [ServiceCategory.GROCERY, ServiceCategory.PHARMACY, ServiceCategory.PARK])

    categories = {s.category for s in services}
    assert ServiceCategory.GROCERY in categories
    assert ServiceCategory.PARK in categories       # partial success still counts
    assert ServiceCategory.PHARMACY not in categories
    assert "provider down" in errors["pharmacy"][0]
    assert "provider down" in errors["park"][0]


def test_fetch_services_reports_progress(monkeypatch):
    monkeypatch.setattr(service_sources, "_PROVIDERS", {"fixture": FixtureServiceSource})
    monkeypatch.setattr(service_sources, "SOURCE_PLAN", {ServiceCategory.GROCERY: ("fixture",)})
    seen = []
    fetch_services([ServiceCategory.GROCERY], on_progress=lambda c, s, e: seen.append((c, len(s))))
    assert seen == [(ServiceCategory.GROCERY, 3)]


# --- cache -------------------------------------------------------------------
def test_service_cache_round_trip(tmp_path, streets):
    from app.service_index import snap_services

    services = snap_services(streets, fixture_services())
    fingerprint = network_fingerprint(streets)
    path = write_cache(services, fingerprint, tmp_path / "services.json", errors={"park": ["x"]})
    payload = read_cache(path)

    assert len(payload["services"]) == len(services)
    assert payload["network_fingerprint"] == fingerprint
    assert payload["errors"] == {"park": ["x"]}
    restored = payload["services"][0]
    assert restored.access_node_id == services[0].access_node_id
    assert restored.access_distance_m == pytest.approx(services[0].access_distance_m)
    assert restored.access_quality == services[0].access_quality


def test_network_fingerprint_changes_with_the_network(streets):
    from app.street_sources.base import StreetNetwork, make_provenance
    import networkx as nx

    other = StreetNetwork(nx.Graph(), make_provenance(mode="fixture", source="t", dataset="t"))
    assert network_fingerprint(streets) != network_fingerprint(other)


def test_reading_a_missing_service_cache_raises(tmp_path):
    with pytest.raises(ServiceSourceError):
        read_cache(tmp_path / "nope.json")


def test_reading_a_corrupt_service_cache_raises(tmp_path):
    path = tmp_path / "services.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ServiceSourceError):
        read_cache(path)


def test_empty_service_cache_raises(tmp_path):
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"services": []}), encoding="utf-8")
    with pytest.raises(ServiceSourceError):
        read_cache(path)


# --- resolution / fallback ---------------------------------------------------
def test_load_falls_back_to_fixture_without_a_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEL_SERVICE_SOURCE", "auto")
    payload = load_services(path=tmp_path / "missing.json")
    assert payload["mode"] == "fixture"
    assert "prepare_data" in payload["fallback_reason"]
    assert payload["services"]


def test_forced_fixture_mode_is_labelled(tmp_path):
    payload = load_services(force_fixture=True, path=tmp_path / "missing.json")
    assert payload["mode"] == "fixture"
    assert payload["fallback_reason"] == "Fixture mode requested"
