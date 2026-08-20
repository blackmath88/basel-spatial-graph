from app.fixtures import fixture_records
from app.graph import build_graph, neighbors

def test_graph_has_relations():
    g=build_graph(fixture_records())
    assert g.number_of_nodes()==7
    assert any(d.get("type")=="IN_AREA" for _,_,d in g.edges(data=True))
    assert any(d.get("type")=="NEAR" for _,_,d in g.edges(data=True))

def test_neighbors():
    g=build_graph(fixture_records())
    result=neighbors(g,"school:1")
    assert result["nodes"]
    assert any(e["type"] in {"IN_AREA","NEAR"} for e in result["edges"])
