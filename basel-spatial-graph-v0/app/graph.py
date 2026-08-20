import math
import networkx as nx
from shapely.geometry import shape, Point

EARTH_M=6371000

def haversine_m(a,b):
    lon1,lat1=a; lon2,lat2=b
    p1,p2=math.radians(lat1),math.radians(lat2)
    dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    h=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*EARTH_M*math.asin(math.sqrt(h))

def centroid_coords(geom):
    g=shape(geom); c=g.centroid
    return (c.x,c.y)

def build_graph(data, near_school_m=500):
    g=nx.MultiDiGraph()
    all_records=[]
    for kind in ("areas","schools","accidents"):
        all_records.extend(data[kind])
    for r in all_records:
        g.add_node(r["id"], **{k:v for k,v in r.items() if k!="id"})

    areas=data["areas"]
    area_shapes=[(a,shape(a["geometry"])) for a in areas]
    for r in data["schools"]+data["accidents"]:
        p=shape(r["geometry"])
        for a,poly in area_shapes:
            if poly.contains(p) or poly.touches(p):
                g.add_edge(r["id"],a["id"],type="IN_AREA",provenance={"derived":True,"method":"point-in-polygon"})
                break

    for i,(a,sa) in enumerate(area_shapes):
        for b,sb in area_shapes[i+1:]:
            if sa.touches(sb):
                prov={"derived":True,"method":"polygon-contiguity"}
                g.add_edge(a["id"],b["id"],type="ADJACENT_TO",provenance=prov)
                g.add_edge(b["id"],a["id"],type="ADJACENT_TO",provenance=prov)

    schools=[(s,centroid_coords(s["geometry"])) for s in data["schools"]]
    for accident in data["accidents"]:
        ac=centroid_coords(accident["geometry"])
        for school,sc in schools:
            d=haversine_m(ac,sc)
            if d <= near_school_m:
                g.add_edge(accident["id"],school["id"],type="NEAR",distance_m=round(d,1),provenance={"derived":True,"method":"haversine","threshold_m":near_school_m})
    return g

def connect_street_access(g, streets):
    """Materialize stable entity-to-network attachment, never dynamic reachability."""
    for street_id, data in streets.graph.nodes(data=True):
        g.add_node(f"street:{street_id}", **data, geometry={"type":"Point","coordinates":[data["lon"],data["lat"]]}, provenance=streets.provenance)
    for node_id, data in list(g.nodes(data=True)):
        if data.get("type") not in {"School", "Accident"} or data.get("geometry", {}).get("type") != "Point":
            continue
        lon, lat = data["geometry"]["coordinates"]
        street_id, distance = streets.nearest_node(lat, lon)
        g.add_edge(node_id, f"street:{street_id}", type="ACCESS_POINT", distance_m=round(distance, 1), provenance={"derived":True,"method":"nearest walking-network node"})
    return g

def node_payload(g,node_id):
    d=dict(g.nodes[node_id]); d["id"]=node_id; return d

def edge_payload(u,v,d):
    return {"source":u,"target":v,**d}

def neighbors(g,node_id):
    edges=[]; seen=set()
    for u,v,d in list(g.out_edges(node_id,data=True))+list(g.in_edges(node_id,data=True)):
        key=(u,v,d.get("type"))
        if key not in seen:
            seen.add(key); edges.append(edge_payload(u,v,d))
    ids={node_id}
    for e in edges: ids|={e["source"],e["target"]}
    return {"nodes":[node_payload(g,n) for n in ids],"edges":edges}

def subgraph(g,node_id,depth=1):
    und=g.to_undirected()
    ids=set(nx.single_source_shortest_path_length(und,node_id,cutoff=depth).keys())
    sg=g.subgraph(ids)
    return {"nodes":[node_payload(g,n) for n in sg.nodes],"edges":[edge_payload(u,v,d) for u,v,d in sg.edges(data=True)]}
