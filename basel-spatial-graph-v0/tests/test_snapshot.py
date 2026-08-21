"""The frozen-snapshot bookkeeping: which data state the server is running on."""
import json

import pytest
from fastapi.testclient import TestClient

from app import snapshot
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def one_artefact(tmp_path, monkeypatch):
    """A single fake artefact, described by a manifest next to it."""
    artefact = tmp_path / "thing.json"
    artefact.write_text('{"value": 1}', encoding="utf-8")
    monkeypatch.setattr(snapshot, "ARTIFACTS", {
        "thing": {"path": artefact, "consumed_by": "a test", "runtime": True},
    })
    manifest = tmp_path / "SNAPSHOT.json"
    manifest.write_text(json.dumps({
        "format_version": snapshot.FORMAT_VERSION,
        "created_at": "2026-08-20T00:00:00+00:00",
        "refresh_command": "python -m app.prepare_data",
        "artifacts": {"thing": {"path": "thing.json", **snapshot.digest(artefact)}},
    }), encoding="utf-8")
    return artefact, manifest


# --- the three states --------------------------------------------------------
def test_matching_artefact_is_frozen(one_artefact):
    _, manifest = one_artefact
    assert snapshot.RuntimeSnapshot.load(manifest).state("thing") == snapshot.FROZEN


def test_changed_artefact_is_local(one_artefact):
    artefact, manifest = one_artefact
    artefact.write_text('{"value": 2}', encoding="utf-8")
    assert snapshot.RuntimeSnapshot.load(manifest).state("thing") == snapshot.LOCAL


def test_missing_artefact_is_absent(one_artefact):
    artefact, manifest = one_artefact
    artefact.unlink()
    assert snapshot.RuntimeSnapshot.load(manifest).state("thing") == snapshot.ABSENT


def test_without_a_manifest_nothing_is_frozen(one_artefact, tmp_path):
    state = snapshot.RuntimeSnapshot.load(tmp_path / "absent.json")
    assert state.available is False
    assert state.state("thing") == snapshot.LOCAL


def test_fixture_mode_wins_over_the_files_on_disk(one_artefact):
    """A server that fell back is not running the snapshot, whatever is on disk."""
    _, manifest = one_artefact
    state = snapshot.RuntimeSnapshot.load(manifest)
    assert state.state("thing") == snapshot.FROZEN
    assert state.state("thing", mode="fixture") == snapshot.FIXTURE


def test_a_manifest_from_another_format_version_is_ignored(one_artefact, tmp_path):
    _, manifest = one_artefact
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["format_version"] = snapshot.FORMAT_VERSION + 1
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert snapshot.read_manifest(manifest) is None


# --- the block a status response embeds --------------------------------------
def test_block_states_the_refresh_command_and_never_claims_currency(one_artefact):
    _, manifest = one_artefact
    block = snapshot.RuntimeSnapshot.load(manifest).block("thing")
    assert block["state"] == snapshot.FROZEN
    assert block["frozen"] is True
    assert block["refresh_command"] == "python -m app.prepare_data"
    assert "not current" in block["explanation"]


def test_fixture_block_says_it_is_not_basel(one_artefact):
    _, manifest = one_artefact
    block = snapshot.RuntimeSnapshot.load(manifest).block("thing", mode="fixture")
    assert block["frozen"] is False
    assert "not Basel" in block["explanation"]


def test_check_reports_a_verdict_per_artefact(one_artefact):
    artefact, manifest = one_artefact
    assert snapshot.check(manifest)["matches"] is True
    artefact.write_text("{}", encoding="utf-8")
    result = snapshot.check(manifest)
    assert result["matches"] is False
    assert result["artifacts"]["thing"]["verdict"] == "differs"


# --- what the API exposes ----------------------------------------------------
def test_health_reports_a_snapshot_block(client):
    snapshot_block = client.get("/health").json()["snapshot"]
    assert set(snapshot_block) >= {"state", "label", "is_frozen_snapshot",
                                   "refresh_command", "artifacts"}


def test_health_reports_a_data_state_per_subsystem(client):
    health = client.get("/health").json()
    for key in ("entities", "streets", "bike", "services", "transit", "spatial_graph"):
        assert health[key]["data_state"]["state"] in {
            snapshot.FROZEN, snapshot.LOCAL, snapshot.FIXTURE, snapshot.ABSENT}


def test_the_fixture_server_never_claims_to_run_the_snapshot(client):
    """The suite runs fully in fixture mode, so nothing may report `frozen`."""
    health = client.get("/health").json()
    for key in ("entities", "streets", "bike", "services", "transit", "spatial_graph"):
        assert health[key]["data_state"]["state"] == snapshot.FIXTURE
        assert health[key]["data_state"]["frozen"] is False
    assert health["snapshot"]["is_frozen_snapshot"] is False


def test_data_status_carries_the_snapshot(client):
    body = client.get("/data/status").json()
    assert "snapshot" in body
    assert body["snapshot"]["refresh_command"] == "python -m app.prepare_data"


def test_spatial_graph_status_carries_the_data_state(client):
    state = client.get("/spatial-graph/status").json()["data_state"]
    assert state["state"] == snapshot.FIXTURE


def test_query_provenance_carries_the_data_state(client):
    response = client.post("/spatial-graph/query",
                           json={"start": {"type": "Neighborhood"}, "limit": 1})
    assert response.json()["provenance"]["data_state"]["state"] == snapshot.FIXTURE


def test_standing_question_provenance_carries_the_data_state(client):
    body = client.get("/spatial-graph/questions/q1_poorest_access?category=pharmacy").json()
    assert body["provenance"]["data_state"]["state"] == snapshot.FIXTURE


# --- the manifest itself -----------------------------------------------------
def test_every_declared_artefact_is_a_processed_file():
    """Nothing in the snapshot may point at a raw download cache."""
    for spec in snapshot.ARTIFACTS.values():
        assert spec["path"].parent.name == "processed"


def test_relative_paths_are_repository_relative():
    assert snapshot.relative(snapshot.SNAPSHOT_MANIFEST) == "data/processed/SNAPSHOT.json"
