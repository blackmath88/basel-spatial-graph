"""Entity ingestion: pagination, field mapping, caching, fixture fallback."""
import json

from app import ingest
from app.ingest import load_data, normalize, read_entity_cache, write_entity_cache

AREA_ROW = {
    "wov_id": "09", "wov_name": "Gotthelf", "gemeinde_name": "Basel",
    "geo_shape": {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[7.57, 47.55], [7.58, 47.55], [7.58, 47.56], [7.57, 47.55]]]}},
}
SCHOOL_ROW = {
    "sc_schulstandort": "TS Rittergasse", "sc_adresse": "Rittergasse 4",
    "geo_point_2d": {"lon": 7.5923, "lat": 47.5558},
}
ACCIDENT_ROW = {
    "gml_id": "38911", "vu_typ": "Einbiegeunfall", "vu_jahr": 2025,
    "geo_shape": {"type": "Feature", "geometry": {"type": "Point", "coordinates": [7.59, 47.556]}},
}


def test_normalize_uses_real_dataset_field_names():
    area = normalize("areas", [AREA_ROW])[0]
    assert area["id"] == "area:09"
    assert area["name"] == "Gotthelf"
    assert area["type"] == "Area"
    assert area["geometry"]["type"] == "Polygon"
    assert area["provenance"]["source"] == "data.bs.ch"
    assert area["provenance"]["derived"] is False

    accident = normalize("accidents", [ACCIDENT_ROW])[0]
    assert accident["id"] == "accident:38911"
    assert accident["name"] == "Einbiegeunfall"


def test_geo_point_2d_becomes_a_point_geometry():
    school = normalize("schools", [SCHOOL_ROW])[0]
    assert school["geometry"] == {"type": "Point", "coordinates": [7.5923, 47.5558]}
    assert school["name"] == "TS Rittergasse"


def test_keyless_records_get_a_position_stable_id():
    first = normalize("schools", [SCHOOL_ROW])[0]["id"]
    other = {"sc_schulstandort": "Elsewhere", "geo_point_2d": {"lon": 7.60, "lat": 47.56}}
    second = normalize("schools", [other, SCHOOL_ROW])[1]["id"]
    assert first == second  # row order must not change the id
    assert first.startswith("school:")


def test_records_without_geometry_are_skipped():
    assert normalize("schools", [{"sc_schulstandort": "no geometry"}]) == []


def test_fetch_dataset_pages_through_the_100_record_cap(monkeypatch):
    """The Opendatasoft API refuses limit > 100, so ingestion must paginate."""
    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, rows):
            self._rows = rows

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": self._rows}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            calls.append(params)
            assert params["limit"] <= 100
            start = params["offset"]
            return FakeResponse([{"gml_id": i, "geo_point_2d": {"lon": 7.59, "lat": 47.55}}
                                 for i in range(start, min(start + params["limit"], 250))])

    monkeypatch.setattr(ingest.httpx, "Client", FakeClient)
    rows = ingest.fetch_dataset("100120", 250)
    assert len(rows) == 250
    assert [c["offset"] for c in calls] == [0, 100, 200]


def test_fetch_dataset_stops_when_a_page_is_short(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"gml_id": 1, "geo_point_2d": {"lon": 7.59, "lat": 47.55}}]}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            return FakeResponse()

    monkeypatch.setattr(ingest.httpx, "Client", FakeClient)
    assert len(ingest.fetch_dataset("100120", 500)) == 1


def test_entity_cache_round_trip(tmp_path):
    data = {
        "areas": normalize("areas", [AREA_ROW]),
        "schools": normalize("schools", [SCHOOL_ROW]),
        "accidents": normalize("accidents", [ACCIDENT_ROW]),
        "mode": "live",
        "source": "data.bs.ch",
    }
    path = write_entity_cache(data, tmp_path / "entities.json")
    loaded = read_entity_cache(path)
    assert loaded["mode"] == "live"
    assert loaded["schools"][0]["name"] == "TS Rittergasse"
    assert loaded["cache_path"] == str(path)


def test_load_data_falls_back_to_fixture_without_a_cache(tmp_path):
    data = load_data(path=tmp_path / "missing.json")
    assert data["mode"] == "fixture"
    assert "prepare_data" in data["fallback_reason"]


def test_load_data_reports_a_broken_cache_instead_of_claiming_live(tmp_path):
    path = tmp_path / "entities.json"
    path.write_text(json.dumps({"areas": [], "schools": [], "accidents": []}), encoding="utf-8")
    data = load_data(path=path)
    assert data["mode"] == "fixture"
    assert data["fallback_reason"]


def test_forced_fixture_mode_is_labelled():
    assert load_data(force_fixture=True)["mode"] == "fixture"
