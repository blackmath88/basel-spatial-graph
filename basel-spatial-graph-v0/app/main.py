import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .config import STATIC_DIR
from .ingest import load_data
from .graph import build_graph, centroid_coords, connect_street_access, node_payload, neighbors, subgraph
from .street import load_street_network
from .accessibility import WalkingAccessibilityService

DATA=load_data(force_fixture=os.getenv("BASEL_GRAPH_FIXTURE","0")=="1")
GRAPH=build_graph(DATA)
STREETS=load_street_network(force_fixture=os.getenv("BASEL_GRAPH_FIXTURE","0")=="1")
ACCESSIBILITY=WalkingAccessibilityService(STREETS, GRAPH)
connect_street_access(GRAPH, STREETS)

app=FastAPI(title="15-Minute Basel Spatial Graph",version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def root(): return FileResponse(STATIC_DIR/"index.html")

@app.get("/health")
def health():
    return {"ok":True,"data_mode":DATA.get("mode"),"fallback_reason":DATA.get("fallback_reason"),"street_network_mode":STREETS.provenance["mode"],"street_fallback_reason":STREETS.fallback_reason,"nodes":GRAPH.number_of_nodes(),"edges":GRAPH.number_of_edges(),"street_nodes":STREETS.graph.number_of_nodes(),"street_edges":STREETS.graph.number_of_edges()}

@app.get("/accessibility/walk")
def walking_accessibility(lat:float=Query(...,ge=-90,le=90), lon:float=Query(...,ge=-180,le=180), minutes:float=Query(15,gt=0,le=60), walking_speed_kmh:float=Query(4.8,gt=0,le=12)):
    return ACCESSIBILITY.calculate(lat,lon,minutes,walking_speed_kmh)

@app.get("/entities/{entity_type}/{entity_id:path}/accessibility")
def entity_accessibility(entity_type:str, entity_id:str, mode:str="walk", minutes:float=Query(15,gt=0,le=60), walking_speed_kmh:float=Query(4.8,gt=0,le=12)):
    if mode != "walk": raise HTTPException(400,"Only walking mode is available in V0.2")
    if entity_id not in GRAPH: raise HTTPException(404,"Entity not found")
    geometry=GRAPH.nodes[entity_id].get("geometry",{})
    if geometry.get("type")=="Point": lon,lat=geometry["coordinates"]
    else:
        lon,lat=centroid_coords(geometry)
    return ACCESSIBILITY.calculate(lat,lon,minutes,walking_speed_kmh)

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
