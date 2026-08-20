"""One-off data preparation: download, normalize, project and cache.

    python -m app.prepare_data            # walking network + Basel entities
    python -m app.prepare_data --refresh  # ignore caches and re-download
    python -m app.prepare_data --fixture  # write nothing live; report fixture mode
    python -m app.prepare_data --network-only

The API server never downloads anything: it loads these caches at startup.
"""
from __future__ import annotations

import argparse
import sys
import traceback

from .config import ENTITY_CACHE, ENTITY_LIMITS, OSMNX_CACHE_DIR
from .errors import BaselGraphError
from .ingest import fetch_entities, load_data, write_entity_cache
from .street_sources import OSMnxWalkingNetworkSource, fixture_street_network

LIVE_BANNER = "LIVE"
FIXTURE_BANNER = "FIXTURE (synthetic — not real Basel data)"


def _n(value) -> str:
    return f"{value:,}"


def _rel(path) -> str:
    from .config import ROOT

    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def prepare_network(refresh: bool = False, fixture: bool = False, verbose: bool = True) -> dict:
    print("Preparing Basel walking network...\n")
    if fixture:
        network = fixture_street_network("Fixture mode requested on the command line")
        _print_network(network, cached=None, used_cache=False)
        return {"status": FIXTURE_BANNER, "network": network}

    source = OSMnxWalkingNetworkSource(allow_download=True, refresh=refresh)
    try:
        network = source.load()
    except BaselGraphError as exc:
        print(f"  live source FAILED: {exc.message}")
        for attempt in exc.details.get("attempts", []):
            print(f"    - {attempt}")
        print("\n  Falling back to the synthetic fixture network.")
        print("  Nothing about Basel geography below is real.\n")
        network = fixture_street_network(exc.message)
        _print_network(network, cached=None, used_cache=False)
        return {"status": FIXTURE_BANNER, "network": network, "error": exc.message}
    except Exception as exc:  # unexpected: show it, do not pretend it worked
        if verbose:
            traceback.print_exc()
        network = fixture_street_network(str(exc))
        print(f"\n  live source FAILED unexpectedly: {exc}")
        _print_network(network, cached=None, used_cache=False)
        return {"status": FIXTURE_BANNER, "network": network, "error": str(exc)}

    _print_network(network, cached=source.cache_path, used_cache=source.used_cache)
    return {"status": LIVE_BANNER, "network": network}


def _print_network(network, cached, used_cache: bool) -> None:
    stats = network.stats()
    provenance = network.provenance
    print(f"  source:  {stats['source']}")
    if provenance.get("place"):
        print(f"  place:   {provenance['place']}")
    print(f"  nodes:   {_n(stats['nodes'])}")
    print(f"  edges:   {_n(stats['edges'])}")
    print(f"  length:  {stats['total_length_m'] / 1000:,.1f} km of walkable ways")
    print(f"  CRS:     {stats['crs']} (distances computed in {stats['metric_crs']})")
    if stats["dropped_edges"]:
        print(f"  dropped: {_n(stats['dropped_edges'])} edges without a usable length")
    if cached is not None:
        print(f"  cached:  {_rel(cached)} ({'reused existing cache' if used_cache else 'written'})")
        print(f"  osm http cache: {_rel(OSMNX_CACHE_DIR)}")
    else:
        print("  cached:  not written (fixture data is generated in memory)")
    if provenance.get("retrieved_at"):
        print(f"  retrieved: {provenance['retrieved_at']}")
    if network.fallback_reason:
        print(f"  reason:  {network.fallback_reason}")


def prepare_entities(refresh: bool = False, fixture: bool = False) -> dict:
    print("\nPreparing Basel entities (areas, schools, accidents)...\n")
    if fixture:
        print("  source:  synthetic fixture (requested)")
        return {"status": FIXTURE_BANNER}
    if ENTITY_CACHE.exists() and not refresh:
        data = load_data()
        if data.get("mode") == "live":
            _print_entities(data, cached=True)
            return {"status": LIVE_BANNER}
    try:
        data = fetch_entities()
    except Exception as exc:
        print(f"  live source FAILED: {exc}")
        print("  The app will fall back to synthetic fixture entities.")
        return {"status": FIXTURE_BANNER, "error": str(exc)}
    write_entity_cache(data)
    _print_entities(data, cached=False)
    return {"status": LIVE_BANNER}


def _print_entities(data, cached: bool) -> None:
    print("  source:  data.bs.ch (Open Government Data Basel-Stadt)")
    for kind in ("areas", "schools", "accidents"):
        limit = ENTITY_LIMITS[kind]
        note = f" (capped at {_n(limit)})" if len(data[kind]) >= limit else ""
        print(f"  {kind:<10} {_n(len(data[kind]))}{note}")
    print(f"  cached:  {_rel(ENTITY_CACHE)} ({'reused existing cache' if cached else 'written'})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prepare Basel walking network and entity caches.")
    parser.add_argument("--refresh", action="store_true", help="ignore existing caches and re-download")
    parser.add_argument("--fixture", action="store_true", help="do not touch the network; report fixture mode")
    parser.add_argument("--network-only", action="store_true", help="skip the entity datasets")
    parser.add_argument("--entities-only", action="store_true", help="skip the walking network")
    args = parser.parse_args(argv)

    network_status = entity_status = None
    if not args.entities_only:
        network_status = prepare_network(refresh=args.refresh, fixture=args.fixture)["status"]
    if not args.network_only:
        entity_status = prepare_entities(refresh=args.refresh, fixture=args.fixture)["status"]

    print("\n" + "-" * 58)
    if network_status:
        print(f"status  streets:  {network_status}")
    if entity_status:
        print(f"status  entities: {entity_status}")
    print("-" * 58)
    print("\nStart the app with:  uvicorn app.main:app --reload")
    failed = FIXTURE_BANNER in {network_status, entity_status}
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
