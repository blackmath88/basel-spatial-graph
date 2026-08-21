"""The generated data-quality report."""
import json

from app.data_quality import (build_report, compact_snapshot, concise, read_report,
                              relevant_caveats, write_report)
from app.service_index import ServiceIndex, snap_services
from app.service_model import ServiceCategory, ServiceLocation
from app.fixtures import fixture_records


def _service(sid, category=ServiceCategory.GROCERY, name="Shop", lon=7.5745, lat=47.5505):
    return ServiceLocation(id=sid, category=category, lon=lon, lat=lat, name=name,
                           source="test source", source_dataset="test dataset", source_id=sid)


def test_report_counts_entities_by_category(streets, service_index):
    report = build_report(streets, fixture_records(), service_index)
    categories = report["services"]["categories"]
    assert categories["grocery"]["count"] == 3
    assert categories["grocery"]["sources"] == ["synthetic fixture"]
    assert report["services"]["total"] == len(service_index.services)
    assert report["entities"]["counts"]["areas"] == 2


def test_report_records_network_and_generation_time(streets, service_index):
    report = build_report(streets, None, service_index)
    assert report["network"]["nodes"] == streets.graph.number_of_nodes()
    assert report["network"]["metric_crs"] == "EPSG:2056"
    assert report["generated_at"].endswith("+00:00")


def test_report_counts_missing_names(streets):
    services = [_service("a"), _service("b", name=None)]
    snap_services(streets, services)
    report = build_report(streets, None, ServiceIndex(services))
    grocery = report["services"]["categories"]["grocery"]
    assert grocery["missing_name"] == 1
    assert grocery["missing_name_ratio"] == 0.5
    assert any("no upstream name" in w for w in report["warnings"])


def test_report_counts_failed_and_poor_snaps(streets):
    services = [
        _service("good", lon=7.5745, lat=47.5505),
        _service("poor", lon=7.5745, lat=47.5522),     # ~190 m off the grid
        _service("detached", lon=7.7000, lat=47.6200),  # far outside
    ]
    snap_services(streets, services)
    report = build_report(streets, None, ServiceIndex(services))
    grocery = report["services"]["categories"]["grocery"]
    assert grocery["failed_snaps"] == 1
    assert grocery["poor_snaps"] >= 1
    assert grocery["routable"] == 2
    assert grocery["snap_distance_m"]["max"] > grocery["snap_distance_m"]["median"]
    assert any("could not be attached" in w for w in report["warnings"])


def test_report_flags_duplicate_candidates(streets):
    services = [_service("a"), _service("b", lon=7.57451, lat=47.55051)]
    snap_services(streets, services)
    report = build_report(streets, None, ServiceIndex(services))
    grocery = report["services"]["categories"]["grocery"]
    assert grocery["duplicate_candidates"] == 1
    assert grocery["duplicate_samples"][0]["ids"] == ["a", "b"]


def test_report_warns_loudly_about_fixture_data(streets, service_index):
    report = build_report(streets, fixture_records(), service_index)
    warnings = " ".join(report["warnings"])
    assert "FIXTURE" in warnings


def test_report_surfaces_source_failures(streets):
    index = ServiceIndex([], mode="live", source_errors={"pharmacy": ["osm: timeout"]})
    report = build_report(streets, None, index)
    assert any("osm: timeout" in w for w in report["warnings"])


def test_report_round_trips_to_disk(tmp_path, streets, service_index):
    report = build_report(streets, fixture_records(), service_index)
    path = write_report(report, tmp_path / "data_quality.json")
    assert json.loads(path.read_text())["services"]["total"] == report["services"]["total"]
    assert read_report(path)["generated_at"] == report["generated_at"]


def test_reading_a_missing_or_broken_report_returns_none(tmp_path):
    assert read_report(tmp_path / "nope.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert read_report(broken) is None


def test_concise_form_is_small_and_explains_itself(streets, service_index):
    summary = concise(build_report(streets, fixture_records(), service_index))
    assert summary["available"] is True
    assert summary["services"]["by_category"]["grocery"]["count"] == 3
    assert "duplicate_candidates" in summary["services"]["by_category"]["grocery"]
    assert summary["warning_count"] >= 1
    assert len(summary["warnings"]) <= 20


def test_concise_form_without_a_report_tells_you_what_to_run():
    summary = concise(None)
    assert summary["available"] is False
    assert "prepare_data" in summary["hint"]


def test_quality_selector_is_scoped_and_structured(streets):
    services = [_service("p", category=ServiceCategory.PHARMACY, lon=7.7, lat=47.62),
                _service("g", category=ServiceCategory.GROCERY, lon=7.7, lat=47.62)]
    snap_services(streets, services)
    index = ServiceIndex(services, mode="fixture",
                         source_errors={"grocery": ["irrelevant failure"]})
    snapshot = compact_snapshot(build_report(streets, None, index))
    selected = relevant_caveats(snapshot, categories=["pharmacy"], networks=["walk"])
    codes = {row["code"] for row in selected["caveats"]}
    assert "service_snap_failures" in codes
    assert "network_not_live" in codes
    assert "service_source_failure" not in codes
    assert all(row["scope"].get("category") != "grocery"
               for row in selected["caveats"])


def test_quality_selector_has_explicit_unavailable_state():
    assert relevant_caveats(None, categories=["pharmacy"]) == {
        "available": False, "caveats": []}


def test_data_status_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app

    body = TestClient(app).get("/data/status").json()
    assert body["available"] is True
    assert "by_category" in body["services"]
