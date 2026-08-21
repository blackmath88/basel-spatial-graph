"""Build the heterogeneous Basel graph from already-prepared artefacts.

    python -m app.prepare_spatial_graph
    python -m app.prepare_spatial_graph --fixture

This reads the caches `python -m app.prepare_data` writes and produces one more:
`data/processed/basel_spatial_graph.json`. It downloads nothing.
"""
from __future__ import annotations

import argparse
import sys

from .config import ROOT, SPATIAL_GRAPH_CACHE
from .ingest import load_data
from .population import load_population
from .service_index import index_from_payload
from .service_sources import load_services
from .spatial_graph.builder import build_spatial_graph
from .street_sources import load_network
from .transit_sources import load_transit
from .data_quality import compact_snapshot, read_report


def _rel(path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def prepare(fixture: bool = False, path=None, verbose: bool = True):
    say = (lambda message: print(f"  {message}", flush=True)) if verbose else (lambda m: None)
    print("Building the Basel spatial graph...\n")

    networks = {name: load_network(name, force_fixture=fixture) for name in ("walk", "bike")}
    entities = load_data(force_fixture=fixture)
    services = index_from_payload(load_services(force_fixture=fixture), networks)
    transit = load_transit(force_fixture=fixture).attach_to_network(networks["walk"])
    population = load_population(force_fixture=fixture)

    for label, mode, reason in (
        ("walking network", networks["walk"].mode, networks["walk"].fallback_reason),
        ("cycling network", networks["bike"].mode, networks["bike"].fallback_reason),
        ("entities", entities.get("mode"), entities.get("fallback_reason")),
        ("services", services.mode, services.fallback_reason),
        ("timetable", transit.mode, transit.fallback_reason),
        ("population", population.get("mode"), population.get("fallback_reason")),
    ):
        marker = "LIVE " if mode == "live" else "FIXTURE"
        print(f"  input  {label:<18} {marker}" + (f"  ({reason})" if reason else ""))
    print()

    graph = build_spatial_graph(
        entities, services, transit, population, networks, progress=say,
        data_quality=compact_snapshot(read_report()))

    print("\n  node types")
    for name, count in graph.node_counts().items():
        print(f"    {name:<24} {count:>7,}")
    print("  relation types")
    for name, count in graph.edge_counts().items():
        print(f"    {name:<24} {count:>7,}")

    written = graph.save(path or SPATIAL_GRAPH_CACHE)
    size = written.stat().st_size / 1e6
    print(f"\n  cached:  {_rel(written)} ({size:,.1f} MB)")
    print(f"  origin:  {graph.metadata['origin_method'][:96]}…")
    for warning in graph.metadata.get("warnings", []):
        print(f"  ! {warning}")

    status = "LIVE" if graph.metadata["mode"] == "live" else "FIXTURE (synthetic — not real Basel data)"
    print("\n" + "-" * 58)
    print(f"status  spatial graph: {status}")
    print("-" * 58)
    print("\nExplore it with:   python -m app.spatial_graph.cli describe")
    print("Serve it with:     uvicorn app.main:app --reload")
    return graph


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the heterogeneous Basel spatial graph.")
    parser.add_argument("--fixture", action="store_true", help="build from synthetic data")
    parser.add_argument("--out", default=None, help="write somewhere other than the default cache")
    args = parser.parse_args(argv)
    graph = prepare(fixture=args.fixture, path=args.out)
    return 0 if graph.metadata["mode"] == "live" else 1


if __name__ == "__main__":
    sys.exit(main())
