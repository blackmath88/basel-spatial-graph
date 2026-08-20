import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .config import STATIC_DIR
from .ingest import load_data
from .graph import build_graph, node_payload, neighbors, subgraph

DATA=load_data(force_fixture=os.getenv("BASEL_GRAPH_FIXTURE","0")=="1")
GRAPH=build_graph(DATA)

app=FastAPI(title="Basel Spatial Graph",version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def root(): return FileResponse(STATIC_DIR/"index.html")

@app.get("/health")
def health():
    return {"ok":True,"data_mode":DATA.get("mode"),"fallback_reason":DATA.get("fallback_reason"),"nodes":GRAPH.number_of_nodes(),"edges":GRAPH.number_of_edges()}

@app.get("/entities/{entity_type}")
def entities(entity_type:str):
    t={"areas":"Area","schools":"School","accidents":"Accident","area":"Area","school":"School","accident":"Accident"}.get(entity_type.lower())
    if not t: raise HTTPException(404,"Unknown entity type")
    return [node_payload(GRAPH,n) for n,d in GRAPH.nodes(data=True) if d.get("type")==t]

@app.get("/entities/{entity_type}/{entity_id:path}")
def entity(entity_type:str,entity_id:str):
    if entity_id not in GRAPH: raise HTTPException(404,"Entity not found")
    return node_payload(GRAPH,entity_id)

@app.get("/graph/neighbors/{node_id:path}")
def graph_neighbors(node_id:str):
    if node_id not in GRAPH: raise HTTPException(404,"Node not found")
    return neighbors(GRAPH,node_id)

@app.get("/graph/subgraph/{node_id:path}")
def graph_subgraph(node_id:str, depth:int=Query(1,ge=1,le=4)):
    if node_id not in GRAPH: raise HTTPException(404,"Node not found")
    return subgraph(GRAPH,node_id,depth)

@app.get("/analysis/areas-by-accidents")
def areas_by_accidents():
    rows=[]
    for n,d in GRAPH.nodes(data=True):
        if d.get("type")!="Area": continue
        count=sum(1 for u,v,e in GRAPH.in_edges(n,data=True) if e.get("type")=="IN_AREA" and GRAPH.nodes[u].get("type")=="Accident")
        rows.append({"id":n,"name":d.get("name"),"accident_count":count})
    return sorted(rows,key=lambda x:x["accident_count"],reverse=True)

@app.get("/analysis/schools-by-nearby-accidents")
def schools_by_nearby_accidents():
    rows=[]
    for n,d in GRAPH.nodes(data=True):
        if d.get("type")!="School": continue
        incoming=[(u,e) for u,v,e in GRAPH.in_edges(n,data=True) if e.get("type")=="NEAR" and GRAPH.nodes[u].get("type")=="Accident"]
        rows.append({"id":n,"name":d.get("name"),"nearby_accident_count":len(incoming),"accidents":[{"id":u,"distance_m":e.get("distance_m")} for u,e in incoming]})
    return sorted(rows,key=lambda x:x["nearby_accident_count"],reverse=True)
