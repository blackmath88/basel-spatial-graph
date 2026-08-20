"""Entity graph relations and the entity-to-network attachment."""
from app.fixtures import fixture_records
from app.graph import build_graph, connect_street_access, haversine_m, neighbors, subgraph


def test_graph_has_relations():
    g = build_graph(fixture_records())
    assert g.number_of_nodes() == 7
    assert any(d.get("type") == "IN_AREA" for _, _, d in g.edges(data=True))
    assert any(d.get("type") == "NEAR" for _, _, d in g.edges(data=True))


def test_near_edges_carry_distance_and_provenance():
    g = build_graph(fixture_records(), near_school_m=500)
    near = [d for _, _, d in g.edges(data=True) if d.get("type") == "NEAR"]
    assert near
    for edge in near:
        assert 0 <= edge["distance_m"] <= 500
        assert edge["provenance"]["method"] == "haversine"
        assert edge["provenance"]["derived"] is True


def test_near_threshold_is_respected():
    assert not [d for _, _, d in build_graph(fixture_records(), near_school_m=1).edges(data=True)
                if d.get("type") == "NEAR"]


def test_neighbors():
    g = build_graph(fixture_records())
    result = neighbors(g, "school:1")
    assert result["nodes"]
    assert any(e["type"] in {"IN_AREA", "NEAR"} for e in result["edges"])


def test_subgraph_depth():
    g = build_graph(fixture_records())
    shallow = subgraph(g, "school:1", depth=1)
    deep = subgraph(g, "school:1", depth=3)
    assert len(deep["nodes"]) >= len(shallow["nodes"])


def test_access_points_attach_entities_to_the_network(streets):
    g = build_graph(fixture_records())
    connect_street_access(g, streets)
    access = [(u, v, d) for u, v, d in g.edges(data=True) if d.get("type") == "ACCESS_POINT"]
    assert len(access) == 5  # 2 schools + 3 accidents
    for u, v, d in access:
        assert v.startswith("street:")
        assert d["distance_m"] >= 0
        assert g.nodes[v]["type"] == "StreetNode"


def test_only_referenced_street_nodes_are_materialized(streets):
    """The entity graph must not mirror an entire city network."""
    g = build_graph(fixture_records())
    connect_street_access(g, streets)
    street_nodes = [n for n, d in g.nodes(data=True) if d.get("type") == "StreetNode"]
    assert 0 < len(street_nodes) < streets.graph.number_of_nodes()


def test_entities_too_far_from_the_network_are_not_attached(streets):
    records = fixture_records()
    records["schools"][0]["geometry"] = {"type": "Point", "coordinates": [6.14, 46.20]}  # Geneva
    g = build_graph(records)
    connect_street_access(g, streets, max_distance_m=500)
    attached = {u for u, v, d in g.edges(data=True) if d.get("type") == "ACCESS_POINT"}
    assert "school:1" not in attached


def test_haversine_matches_known_distance():
    # Basel SBB -> Barfüsserplatz is about 900 m as the crow flies.
    distance = haversine_m((7.5893, 47.5476), (7.5906, 47.5556))
    assert 850 < distance < 950
