"""Does City2Graph build our heterogeneous graph better than we do?

Run with the isolated Python 3.12+ environment described in docs/CITY2GRAPH.md,
not with the project's interpreter:

    python3.14 -m venv .c2g && .c2g/bin/pip install city2graph
    python -m experiments.city2graph_spike --export      # project interpreter
    .c2g/bin/python experiments/city2graph_spike.py      # spike interpreter

The export step writes our own graph out as GeoJSON; the spike step rebuilds the
same structural relations with City2Graph and compares the counts. The point is
evidence, not opinion: if it reproduces our edges we should say so.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "city2graph_data"


def export(out: Path = DATA) -> Path:
    """Write our prepared graph out as plain GeoJSON, using the project's env."""
    sys.path.insert(0, str(HERE.parent))
    from app.config import SPATIAL_GRAPH_CACHE
    from app.spatial_graph.model import NetworkXSpatialGraph

    graph = NetworkXSpatialGraph.load(SPATIAL_GRAPH_CACHE)
    out.mkdir(parents=True, exist_ok=True)

    def features(node_type, geometry):
        return [{"type": "Feature", "geometry": geometry(node),
                 "properties": {k: v for k, v in node.items()
                                if k not in {"geometry", "provenance"}}}
                for node in graph.nodes_of_type(node_type)]

    layers = {
        "neighborhoods": features("Neighborhood", lambda n: n["geometry"]),
        "services": features(
            "ServiceLocation",
            lambda n: {"type": "Point", "coordinates": [n["lon"], n["lat"]]}),
        "stops": features(
            "TransitStop",
            lambda n: {"type": "Point", "coordinates": [n["lon"], n["lat"]]}),
    }
    for name, rows in layers.items():
        (out / f"{name}.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": rows}), encoding="utf-8")
    counts = {
        "ADJACENT_TO_directed": graph.edge_counts()["ADJACENT_TO"],
        "ADJACENT_TO_pairs": graph.edge_counts()["ADJACENT_TO"] // 2,
        "HAS_SERVICE": graph.edge_counts()["HAS_SERVICE"],
        "HAS_TRANSIT_STOP": graph.edge_counts()["HAS_TRANSIT_STOP"],
        "neighborhoods": graph.count_of_type("Neighborhood"),
        "services": graph.count_of_type("ServiceLocation"),
        "stops": graph.count_of_type("TransitStop"),
    }
    (out / "ours.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")
    print(f"exported {len(layers)} layers to {out}")
    for name, value in counts.items():
        print(f"  ours  {name:<24} {value:>7,}")
    return out


def compare(data: Path = DATA) -> dict:
    """Rebuild the same relations with City2Graph and report the differences."""
    warnings.filterwarnings("ignore")
    import city2graph as c2g
    import geopandas as gpd

    ours = json.loads((data / "ours.json").read_text())
    areas = gpd.read_file(data / "neighborhoods.geojson").set_index("id")
    services = gpd.read_file(data / "services.geojson").set_index("id")
    stops = gpd.read_file(data / "stops.geojson").set_index("id")
    print(f"city2graph {c2g.__version__} · torch available: {c2g.is_torch_available()}")
    print(f"loaded {len(areas)} neighbourhoods, {len(services)} services, {len(stops)} stops\n")

    findings = {}

    # 1. ADJACENT_TO — our shapely boundary-contact loop.
    start = time.time()
    _, adjacency = c2g.contiguity_graph(areas, contiguity="queen")
    findings["adjacency"] = {
        "city2graph": len(adjacency), "ours": ours["ADJACENT_TO_pairs"],
        "ms": round((time.time() - start) * 1000),
        "match": len(adjacency) == ours["ADJACENT_TO_pairs"],
    }

    # 2. HAS_SERVICE / LOCATED_IN — our STRtree point-in-polygon pass.
    start = time.time()
    _, contained = c2g.group_nodes(areas, services)
    edges = sum(len(frame) for frame in contained.values())
    findings["containment_services"] = {
        "city2graph": edges, "ours": ours["HAS_SERVICE"],
        "ms": round((time.time() - start) * 1000),
        "match": edges == ours["HAS_SERVICE"],
        "edge_types": [str(k) for k in contained],
    }

    start = time.time()
    _, contained_stops = c2g.group_nodes(areas, stops)
    stop_edges = sum(len(frame) for frame in contained_stops.values())
    findings["containment_stops"] = {
        "city2graph": stop_edges, "ours": ours["HAS_TRANSIT_STOP"],
        "ms": round((time.time() - start) * 1000),
        "match": stop_edges == ours["HAS_TRANSIT_STOP"],
    }

    # 3. Conversion to NetworkX — what we would build on.
    start = time.time()
    nodes, edge_frames = c2g.group_nodes(areas, services)
    graph = c2g.gdf_to_nx(nodes, edge_frames)
    sample = next(iter(graph.nodes(data=True)))[1]
    findings["conversion"] = {
        "type": type(graph).__name__,
        "nodes": graph.number_of_nodes(), "edges": graph.number_of_edges(),
        "ms": round((time.time() - start) * 1000),
        "node_keys": sorted(sample),
        "keeps_our_type_names": "node_type" in sample,
    }

    # 4. Things we do NOT have: proximity layers between node types.
    start = time.time()
    _, bridged = c2g.bridge_nodes({"Neighborhood": areas, "ServiceLocation": services},
                                  proximity_method="knn", k=3)
    findings["bridge_nodes_knn"] = {
        "edge_types": {str(k): len(v) for k, v in bridged.items()},
        "ms": round((time.time() - start) * 1000),
        "note": "no equivalent in our builder; would be new capability",
    }

    print(json.dumps(findings, indent=2))
    print("\nverdict inputs:")
    for name in ("adjacency", "containment_services", "containment_stops"):
        row = findings[name]
        verdict = "reproduces ours exactly" if row["match"] else "DIFFERS"
        print(f"  {name:<22} c2g={row['city2graph']:>6,}  ours={row['ours']:>6,}  "
              f"{row['ms']:>4} ms  {verdict}")
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export", action="store_true",
                        help="write our graph out as GeoJSON (project interpreter)")
    parser.add_argument("--data", default=str(DATA))
    args = parser.parse_args(argv)
    if args.export:
        export(Path(args.data))
        return 0
    compare(Path(args.data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
