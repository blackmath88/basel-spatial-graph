"""Would DuckDB tables for nodes/edges beat NetworkX for this workload?

The concept document floats DuckDB as a query backend. This asks the narrow,
answerable version of that question: for the filtering, joining and aggregating
that our query layer actually does, is SQL over node/edge tables simpler and
faster than dictionaries over a NetworkX graph?

    python -m app.spatial_graph.cli export experiments/export      # project env
    .c2g/bin/python experiments/duckdb_spike.py                    # env with duckdb

Three queries, run both ways, on identical data.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

EXPORT = Path(__file__).resolve().parent / "export"


def _time(function, repeats: int = 5):
    function()
    start = time.perf_counter()
    for _ in range(repeats):
        result = function()
    return result, (time.perf_counter() - start) / repeats * 1000


def duckdb_side(export: Path) -> dict:
    import duckdb

    con = duckdb.connect()
    # Materialized tables, not views over CSV: otherwise every query re-parses
    # the file and the comparison measures disk reading, not querying.
    load_start = time.perf_counter()
    for name in ("Neighborhood", "ServiceLocation", "TransitStop", "PopulationObservation"):
        con.execute(f"""CREATE TABLE {name} AS
                        SELECT * FROM read_csv_auto('{export}/nodes_{name}.csv')""")
    for name in ("HAS_SERVICE", "ADJACENT_TO", "HAS_TRANSIT_STOP",
                 "HAS_POPULATION_OBSERVATION"):
        con.execute(f"""CREATE TABLE {name} AS
                        SELECT * FROM read_csv_auto('{export}/edges_{name}.csv')""")
    load_ms = (time.perf_counter() - load_start) * 1000

    q1 = """SELECT n.name, n.children, count(s."id:ID") AS pharmacies
            FROM Neighborhood n
            LEFT JOIN HAS_SERVICE e ON e.":START_ID" = n."id:ID"
            LEFT JOIN ServiceLocation s ON s."id:ID" = e.":END_ID"
                 AND s.category = 'pharmacy'
            WHERE n.children > 1400
            GROUP BY 1, 2 ORDER BY pharmacies ASC"""
    q2 = """SELECT n.name, count(*) AS adjacent
            FROM Neighborhood n JOIN ADJACENT_TO a ON a.":START_ID" = n."id:ID"
            GROUP BY 1 ORDER BY adjacent DESC LIMIT 5"""
    q3 = """SELECT p.year, sum(p.children) AS children, sum(p.total) AS total
            FROM PopulationObservation p GROUP BY 1 ORDER BY 1"""

    results = {}
    for name, sql in (("filter_join_aggregate", q1), ("degree_ranking", q2),
                      ("time_series_rollup", q3)):
        rows, ms = _time(lambda sql=sql: con.execute(sql).fetchall())
        results[name] = {"rows": len(rows), "ms": round(ms, 2), "sql_lines": len(sql.splitlines())}
    results["_sample"] = con.execute(q1).fetchall()[:3]
    results["_load_ms"] = round(load_ms, 1)
    return results


def networkx_side() -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.config import SPATIAL_GRAPH_CACHE
    from app.spatial_graph import QueryEngine, QuerySpec
    from app.spatial_graph.model import NetworkXSpatialGraph

    load_start = time.perf_counter()
    graph = NetworkXSpatialGraph.load(SPATIAL_GRAPH_CACHE)
    load_ms = (time.perf_counter() - load_start) * 1000
    engine = QueryEngine(graph)

    spec = QuerySpec.parse({
        "start": {"type": "Neighborhood",
                  "filters": [{"field": "children", "op": "gt", "value": 1400}]},
        "traverse": [{"relation": "HAS_SERVICE", "target_type": "ServiceLocation",
                      "as": "pharmacies",
                      "filters": [{"field": "category", "op": "eq", "value": "pharmacy"}]}],
        "rank": {"by": "pharmacies.count", "order": "asc"},
        "return": ["Neighborhood.name", "Neighborhood.children", "pharmacies.count"],
        "limit": 50,
    })
    results = {}
    answer, ms = _time(lambda: engine.run(spec))
    results["filter_join_aggregate"] = {"rows": answer["count"], "ms": round(ms, 2),
                                        "spec_lines": len(json.dumps(spec.describe(), indent=2)
                                                          .splitlines())}
    degree = QuerySpec.parse({
        "start": {"type": "Neighborhood"},
        "traverse": [{"relation": "ADJACENT_TO", "target_type": "Neighborhood", "as": "adjacent"}],
        "rank": {"by": "adjacent.count", "order": "desc"},
        "return": ["Neighborhood.name", "adjacent.count"], "limit": 5,
    })
    answer, ms = _time(lambda: engine.run(degree))
    results["degree_ranking"] = {"rows": answer["count"], "ms": round(ms, 2)}

    # The rollup our language cannot express: group by a field, sum across nodes.
    def rollup():
        totals = {}
        for node in graph.nodes_of_type("PopulationObservation"):
            row = totals.setdefault(node["year"], {"children": 0, "total": 0})
            row["children"] += node["children"]
            row["total"] += node["total"]
        return sorted(totals.items())

    rows, ms = _time(rollup)
    results["time_series_rollup"] = {"rows": len(rows), "ms": round(ms, 2),
                                     "note": "hand-written Python: the query language has no GROUP BY"}
    results["_sample"] = [tuple(r.values()) for r in engine.run(spec)["results"][:3]]
    results["_load_ms"] = round(load_ms, 1)
    return results


def main() -> int:
    export = Path(sys.argv[1]) if len(sys.argv) > 1 else EXPORT
    if not (export / "nodes_Neighborhood.csv").exists():
        print(f"no export at {export}; run "
              f"`python -m app.spatial_graph.cli export {export}` first")
        return 1
    print(f"data: {export}\n")
    try:
        duck = duckdb_side(export)
    except ImportError:
        print("duckdb is not installed in this interpreter; run the spike env")
        return 1
    net = networkx_side()

    print(f"{'query':<24} {'DuckDB':>10} {'NetworkX':>10}   verdict")
    for name in ("filter_join_aggregate", "degree_ranking", "time_series_rollup"):
        faster = "DuckDB" if duck[name]["ms"] < net[name]["ms"] else "NetworkX"
        print(f"{name:<24} {duck[name]['ms']:>8.2f}ms {net[name]['ms']:>8.2f}ms   "
              f"{faster} faster ({max(duck[name]['ms'], net[name]['ms']) / max(min(duck[name]['ms'], net[name]['ms']), 1e-6):.1f}x)")
    print(f"\nload:  DuckDB tables {duck['_load_ms']:.0f} ms  ·  "
          f"NetworkX artefact {net['_load_ms']:.0f} ms")
    print("\nsame answers:", duck["_sample"][:2] == [tuple(r) for r in net["_sample"][:2]]
          or f"\n  duckdb  {duck['_sample'][:2]}\n  networkx {net['_sample'][:2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
