"""Query the spatial graph from a terminal — no FastAPI, no map.

    python -m app.spatial_graph.cli describe
    python -m app.spatial_graph.cli types
    python -m app.spatial_graph.cli entities Neighborhood --limit 5
    python -m app.spatial_graph.cli ask q1_poorest_access --category pharmacy
    python -m app.spatial_graph.cli query examples/queries/children_pharmacy_transit.json
"""
from __future__ import annotations

import argparse
import json
import sys

from ..errors import BaselGraphError


def _service(fixture: bool = False):
    from ..spatial_graph import SpatialGraphService

    if fixture:
        from .fixtures import fixture_service

        return fixture_service()
    from ..accessibility import CyclingAccessibilityService, WalkingAccessibilityService
    from ..ingest import load_data
    from ..modes import TravelMode
    from ..multimodal import MultimodalAccessibilityService
    from ..service_index import index_from_payload
    from ..service_sources import load_services
    from ..street_sources import load_network
    from ..transit_sources import load_transit

    networks = {name: load_network(name) for name in ("walk", "bike")}
    entities = load_data()
    from ..graph import build_graph

    entity_graph = build_graph(entities)
    services = index_from_payload(load_services(), networks)
    transit = load_transit().attach_to_network(networks["walk"])
    engines = {
        TravelMode.WALK: WalkingAccessibilityService(networks["walk"], entity_graph, services),
        TravelMode.BIKE: CyclingAccessibilityService(networks["bike"], entity_graph, services),
    }
    multimodal = MultimodalAccessibilityService(networks["walk"], transit, entity_graph, services)
    if multimodal.available:
        engines[TravelMode.TRANSIT] = multimodal
    service = SpatialGraphService.load(engines=engines, required=True)
    return service


def _emit(payload, compact: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=None if compact else 2))


def _table(rows, columns) -> None:
    if not rows:
        print("  (no results)")
        return
    widths = [max(len(str(c)), *(len(str(r.get(c, ""))) for r in rows)) for c in columns]
    print("  " + "  ".join(str(c).ljust(w) for c, w in zip(columns, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        print("  " + "  ".join(str(row.get(c, "")).ljust(w) for c, w in zip(columns, widths)))


def main(argv=None) -> int:
    # Global flags are also accepted after the subcommand, which is where
    # people actually type them.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--fixture", action="store_true", help="use the synthetic graph")
    common.add_argument("--compact", action="store_true", help="one-line JSON")

    parser = argparse.ArgumentParser(prog="app.spatial_graph.cli", parents=[common],
                                     description="Query the Basel spatial graph.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help):
        return sub.add_parser(name, help=help, parents=[common])

    add("describe", "the full machine-readable schema")
    add("types", "entity types and counts")
    add("relations", "relation types and counts")
    add("status", "what is loaded and where it came from")

    entities = add("entities", "list entities of one type")
    entities.add_argument("type")
    entities.add_argument("--limit", type=int, default=10)
    entities.add_argument("--geometry", action="store_true")

    show = add("entity", "one entity with its provenance")
    show.add_argument("type")
    show.add_argument("id")
    show.add_argument("--geometry", action="store_true")

    neighbors = add("neighbors", "one entity's typed neighbours")
    neighbors.add_argument("type")
    neighbors.add_argument("id")
    neighbors.add_argument("--relation", default=None)
    neighbors.add_argument("--limit", type=int, default=25)

    query = add("query", "run a query specification file (or - for stdin)")
    query.add_argument("path")

    export = add("export", "write CSV / Cypher for Neo4j, DuckDB or pandas")
    export.add_argument("out", help="output directory")
    export.add_argument("--format", choices=["csv", "cypher", "both"], default="both")
    export.add_argument("--geometry", action="store_true")

    ask = add("ask", "run one of the standing questions")
    ask.add_argument("question", nargs="?", default=None)
    ask.add_argument("--category", default=None)
    ask.add_argument("--mode", default=None)
    ask.add_argument("--minutes", type=float, default=None)
    ask.add_argument("--limit", type=int, default=None)
    ask.add_argument("--min-children", type=int, default=None)
    ask.add_argument("--departure-time", default=None)
    ask.add_argument("--table", action="store_true", help="print a table instead of JSON")

    args = parser.parse_args(argv)
    try:
        service = _service(args.fixture)
        payload = _dispatch(service, args)
    except BaselGraphError as exc:
        print(json.dumps({"error": exc.code, "message": exc.message, "details": exc.details},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    if payload is None:
        return 0
    _emit(payload, args.compact)
    return 0


def _dispatch(service, args):
    from .questions import QUESTIONS

    command = args.command
    if command == "describe":
        return service.schema()
    if command == "types":
        return service.entity_types()
    if command == "relations":
        return service.relation_types()
    if command == "status":
        return service.status()
    if command == "entities":
        return service.entities(args.type, limit=args.limit, include_geometry=args.geometry)
    if command == "entity":
        return service.entity(args.type, args.id, include_geometry=args.geometry)
    if command == "neighbors":
        return service.neighbors(args.type, args.id, relation=args.relation, limit=args.limit)
    if command == "export":
        from pathlib import Path

        from .export import schema_cypher, to_cypher, to_csv

        out = Path(args.out)
        result = {}
        if args.format in {"csv", "both"}:
            result["csv"] = to_csv(service.graph, out, include_geometry=args.geometry)
        if args.format in {"cypher", "both"}:
            out.mkdir(parents=True, exist_ok=True)
            (out / "schema.cypher").write_text(schema_cypher(), encoding="utf-8")
            path = to_cypher(service.graph, out / "graph.cypher", include_geometry=args.geometry)
            result["cypher"] = {"graph": path.name, "schema": "schema.cypher",
                                "bytes": path.stat().st_size}
        return result
    if command == "query":
        from pathlib import Path

        text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
        return service.query(json.loads(text))
    if command == "ask":
        if not args.question:
            print("Available questions:")
            for name, function in QUESTIONS.items():
                summary = (function.__doc__ or "").strip().splitlines()[0]
                print(f"  {name:<26} {summary}")
            return None
        params = {k: v for k, v in (
            ("category", args.category), ("mode", args.mode), ("minutes", args.minutes),
            ("limit", args.limit), ("min_children", args.min_children),
            ("departure_time", args.departure_time),
        ) if v is not None}
        import inspect

        accepted = inspect.signature(QUESTIONS[args.question]).parameters
        params = {k: v for k, v in params.items() if k in accepted}
        answer = service.ask(args.question, **params)
        if args.table:
            print(f"\n{answer['question']}\n")
            rows = answer.get("results", [])
            if rows and isinstance(rows[0], dict):
                columns = [k for k, v in rows[0].items()
                           if not isinstance(v, (dict, list))][:8]
                _table(rows, columns)
            print(f"\n  methodology: {answer.get('methodology', '')[:400]}")
            return None
        return answer
    raise SystemExit(f"unknown command {command}")


if __name__ == "__main__":
    sys.exit(main())
