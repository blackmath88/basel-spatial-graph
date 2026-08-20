import os
os.environ["BASEL_GRAPH_FIXTURE"] = "1"

from fastapi.testclient import TestClient
from app.accessibility import WalkingAccessibilityService
from app.fixtures import fixture_records
from app.graph import build_graph
from app.main import app
from app.street import fixture_street_network, load_street_network
import app.street as street_module

def service(): return WalkingAccessibilityService(fixture_street_network(), build_graph(fixture_records()))

def test_nearest_street_node():
    node, distance = fixture_street_network().nearest_node(47.5501, 7.5741)
    assert node == "fixture:0:0"
    assert distance < 20

def test_time_budget_and_reachable_costs():
    result = service().calculate(47.55, 7.574, minutes=5, speed_kmh=4.8)
    assert result["network"]["distance_budget_m"] == 400
    assert result["network"]["max_network_distance_m"] <= 400

def test_reachable_schools_are_sorted_and_within_budget():
    result = service().calculate(47.557, 7.582, minutes=15, speed_kmh=4.8)
    distances = [s["network_distance_m"] for s in result["reachable_entities"]["schools"]]
    assert distances and distances == sorted(distances)
    assert max(distances) <= result["network"]["distance_budget_m"]

def test_walk_api_response():
    response = TestClient(app).get("/accessibility/walk", params={"lat":47.557,"lon":7.582,"minutes":10})
    assert response.status_code == 200
    body=response.json()
    assert body["mode"] == "walk"
    assert body["geometry"]["type"] == "FeatureCollection"
    assert body["provenance"]["algorithm"] == "NetworkX single-source Dijkstra"

def test_explicit_fixture_fallback():
    network=load_street_network(force_fixture=True)
    assert network.provenance["fixture"] is True
    assert network.graph.number_of_edges() > 0

def test_live_source_failure_falls_back(monkeypatch, tmp_path):
    monkeypatch.delenv("BASEL_STREET_NETWORK_SOURCE", raising=False)
    monkeypatch.setattr(street_module, "STREET_CACHE", tmp_path / "missing.json")
    monkeypatch.setattr(street_module.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    network=load_street_network()
    assert network.provenance["mode"] == "fixture"
    assert network.fallback_reason == "offline"
