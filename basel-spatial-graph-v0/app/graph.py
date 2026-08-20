import math

import networkx as nx
import numpy as np
from shapely.geometry import shape

EARTH_M = 6371000


def haversine_m(a, b):
    lon1, lat1 = a
    lon2, lat2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(h))


def centroid_coords(geom):
    g = shape(geom)
    c = g.centroid
    return (c.x, c.y)


def _near_pairs(accidents, schools, threshold_m):
    """Vectorized accident->school proximity; the live datasets are too big for O(n*m) Python."""
    if not accidents or not schools:
        return []
    acc = np.radians(np.array([centroid_coords(a["geometry"]) for a in accidents], dtype=float))
    sch = np.radians(np.array([centroid_coords(s["geometry"]) for s in schools], dtype=float))
    dlon = acc[:, 0][:, None] - sch[:, 0][None, :]
    dlat = acc[:, 1][:, None] - sch[:, 1][None, :]
    h = np.sin(dlat / 2) ** 2 + np.cos(acc[:, 1])[:, None] * np.cos(sch[:, 1])[None, :] * np.sin(dlon / 2) ** 2
    distances = 2 * EARTH_M * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
    rows, cols = np.nonzero(distances <= threshold_m)
    return [(accidents[i], schools[j], float(distances[i, j])) for i, j in zip(rows, cols)]


def build_graph(data, near_school_m=500):
    g = nx.MultiDiGraph()
    all_records = []
    for kind in ("areas", "schools", "accidents"):
        all_records.extend(data[kind])
    for r in all_records:
        g.add_node(r["id"], **{k: v for k, v in r.items() if k != "id"})

    areas = data["areas"]
    area_shapes = [(a, shape(a["geometry"])) for a in areas]
    for r in data["schools"] + data["accidents"]:
        p = shape(r["geometry"])
        for a, poly in area_shapes:
            if poly.contains(p) or poly.touches(p):
                g.add_edge(r["id"], a["id"], type="IN_AREA", provenance={"derived": True, "method": "point-in-polygon"})
                break

    for i, (a, sa) in enumerate(area_shapes):
        for b, sb in area_shapes[i + 1:]:
            if sa.touches(sb):
                prov = {"derived": True, "method": "polygon-contiguity"}
                g.add_edge(a["id"], b["id"], type="ADJACENT_TO", provenance=prov)
                g.add_edge(b["id"], a["id"], type="ADJACENT_TO", provenance=prov)

    for accident, school, distance in _near_pairs(data["accidents"], data["schools"], near_school_m):
        g.add_edge(
            accident["id"], school["id"], type="NEAR", distance_m=round(distance, 1),
            provenance={"derived": True, "method": "haversine", "threshold_m": near_school_m},
        )
    return g


def connect_street_access(g, streets, max_distance_m=500.0):
    """Materialize stable entity-to-network attachment, never dynamic reachability.

    Only the street nodes actually used as access points are copied into the
    entity graph; mirroring an entire city network here would help nobody.
    """
    targets = [
        (node_id, data["geometry"]["coordinates"])
        for node_id, data in list(g.nodes(data=True))
        if data.get("type") in {"School", "Accident"}
        and isinstance(data.get("geometry"), dict)
        and data["geometry"].get("type") == "Point"
    ]
    if not targets:
        return g
    snapped = streets.nearest_nodes([coords for _, coords in targets])
    for (node_id, _), (street_id, distance) in zip(targets, snapped):
        if distance > max_distance_m:
            continue
        street_key = f"street:{street_id}"
        if street_key not in g:
            data = streets.graph.nodes[street_id]
            g.add_node(
                street_key,
                type="StreetNode",
                name=data.get("name") or street_key,
                lon=data["lon"], lat=data["lat"],
                geometry={"type": "Point", "coordinates": [data["lon"], data["lat"]]},
                provenance=streets.provenance,
            )
        g.add_edge(
            node_id, street_key, type="ACCESS_POINT", distance_m=round(distance, 1),
            provenance={"derived": True, "method": "nearest walking-network node"},
        )
    return g


def node_payload(g, node_id):
    d = dict(g.nodes[node_id])
    d["id"] = node_id
    return d


def edge_payload(u, v, d):
    return {"source": u, "target": v, **d}


def neighbors(g, node_id):
    edges = []
    seen = set()
    for u, v, d in list(g.out_edges(node_id, data=True)) + list(g.in_edges(node_id, data=True)):
        key = (u, v, d.get("type"))
        if key not in seen:
            seen.add(key)
            edges.append(edge_payload(u, v, d))
    ids = {node_id}
    for e in edges:
        ids |= {e["source"], e["target"]}
    return {"nodes": [node_payload(g, n) for n in ids], "edges": edges}


def subgraph(g, node_id, depth=1):
    und = g.to_undirected()
    ids = set(nx.single_source_shortest_path_length(und, node_id, cutoff=depth).keys())
    sg = g.subgraph(ids)
    return {"nodes": [node_payload(g, n) for n in sg.nodes], "edges": [edge_payload(u, v, d) for u, v, d in sg.edges(data=True)]}
