"""One-off data preparation: download, normalize, project, snap and cache.

    python -m app.prepare_data            # network + entities + services + snapping
    python -m app.prepare_data --refresh  # ignore caches and re-download
    python -m app.prepare_data --fixture  # write nothing live; report fixture mode
    python -m app.prepare_data --network-only
    python -m app.prepare_data --services-only

The API server never downloads anything: it loads these caches at startup.
"""
from __future__ import annotations

import argparse
import sys
import traceback

from .config import ENTITY_CACHE, ENTITY_LIMITS, OSMNX_CACHE_DIR, SERVICE_CACHE
from .data_quality import build_report, write_report
from .errors import BaselGraphError
from .ingest import fetch_entities, load_data, write_entity_cache
from .service_index import ServiceIndex, index_from_payload, snap_services
from .service_model import ESSENTIAL_CATEGORIES, ServiceCategory
from .service_sources import (
    SOURCE_PLAN,
    fetch_services,
    fixture_services,
    network_fingerprint,
    read_cache,
)
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


def prepare_services(streets, refresh: bool = False, fixture: bool = False) -> dict:
    """Fetch every service category, snap it to the walking network, cache it."""
    print("\nServices\n")
    if fixture:
        services = fixture_services()
        snap_services(streets, services)
        index = ServiceIndex(services, mode="fixture", fallback_reason="Fixture mode requested")
        _print_services(index, cached=None)
        return {"status": FIXTURE_BANNER, "index": index}

    if SERVICE_CACHE.exists() and not refresh:
        # Read the cache directly: preparation has its own --fixture flag and
        # must not be steered by the server-side BASEL_SERVICE_SOURCE variable.
        try:
            payload = read_cache(SERVICE_CACHE)
            payload["mode"] = "live"
            index = _index_from(payload, streets, resnap_note=True)
            _print_services(index, cached=SERVICE_CACHE, reused=True)
            return {"status": LIVE_BANNER, "index": index}
        except BaselGraphError as exc:
            print(f"  cached services unusable ({exc.message}); fetching again")

    services, errors = fetch_services(on_progress=_print_category)
    if not services:
        print("\n  live service sources FAILED entirely; falling back to the fixture.")
        for category, messages in errors.items():
            for message in messages:
                print(f"    - {category}: {message}")
        services = fixture_services()
        snap_services(streets, services)
        index = ServiceIndex(services, mode="fixture",
                             fallback_reason="; ".join(sum(errors.values(), [])) or "no live services")
        _print_services(index, cached=None)
        return {"status": FIXTURE_BANNER, "index": index, "errors": errors}

    print("\nSnapping services to walking network...")
    snap_services(streets, services)
    poor = sum(1 for s in services if s.access_quality == "poor")
    failed = sum(1 for s in services if not s.is_routable)
    print(f"  done. {len(services):,} attached · {poor:,} poor snaps · {failed:,} not attached")

    from .service_sources import write_cache

    write_cache(services, network_fingerprint(streets), errors=errors)
    index = ServiceIndex(services, mode="live", source_errors=errors)
    _print_services(index, cached=SERVICE_CACHE)
    for category, messages in errors.items():
        for message in messages:
            print(f"  ! {category}: {message}")
    status = FIXTURE_BANNER if errors and len(errors) == len(SOURCE_PLAN) else LIVE_BANNER
    return {"status": status, "index": index, "errors": errors}


def _index_from(payload, streets, resnap_note: bool = False) -> ServiceIndex:
    """Rebuild the index from a cache, re-snapping and rewriting if it went stale."""
    def note(fingerprint):
        if resnap_note:
            print("  ! cached snapping was made against a different walking network; re-snapping")
        from .service_sources import write_cache

        write_cache(payload["services"], fingerprint, errors=payload.get("errors"))

    return index_from_payload(payload, streets, on_resnap=note)


def _print_category(category: ServiceCategory, services, errors) -> None:
    plan = "+".join(SOURCE_PLAN.get(category, ()))
    sources = ", ".join(sorted({s.source for s in services})) or "—"
    print(f"  {category.value:<11} {len(services):>5}  via {plan:<8} {sources}")
    for error in errors:
        print(f"    ! {error}")


def _print_services(index: ServiceIndex, cached, reused: bool = False) -> None:
    for row in index.summary()["categories"]:
        marker = "*" if row["essential"] else " "
        print(f" {marker}{row['label']:<11} {row['count']:>5}  {', '.join(row['sources'])}")
    print(f"\n  total:   {len(index.services):,} service locations")
    essentials = [c.value for c in ESSENTIAL_CATEGORIES if c not in index.by_category]
    if essentials:
        print(f"  MISSING essential categories: {', '.join(essentials)}")
    if cached is not None:
        print(f"  cached:  {_rel(cached)} ({'reused existing cache' if reused else 'written'})")
    else:
        print("  cached:  not written (fixture data is generated in memory)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prepare the Basel walking network, entities and services.")
    parser.add_argument("--refresh", action="store_true", help="ignore existing caches and re-download")
    parser.add_argument("--fixture", action="store_true", help="do not touch the network; report fixture mode")
    parser.add_argument("--network-only", action="store_true", help="only the walking network")
    parser.add_argument("--entities-only", action="store_true", help="only the Basel entity datasets")
    parser.add_argument("--services-only", action="store_true", help="only the service POIs")
    args = parser.parse_args(argv)

    only = args.network_only or args.entities_only or args.services_only
    do_network = args.network_only or not only
    do_entities = args.entities_only or not only
    do_services = args.services_only or not only

    print("Preparing Basel Spatial Graph...\n")
    network_status = entity_status = service_status = None
    streets = entities = index = None

    if do_network or do_services:
        result = prepare_network(refresh=args.refresh, fixture=args.fixture)
        streets = result["network"]
        if do_network:
            network_status = result["status"]
    if do_entities:
        entity_status = prepare_entities(refresh=args.refresh, fixture=args.fixture)["status"]
        entities = load_data(force_fixture=args.fixture)
    if do_services:
        result = prepare_services(streets, refresh=args.refresh, fixture=args.fixture)
        service_status = result["status"]
        index = result["index"]

    report = build_report(streets, entities, index)
    path = write_report(report)
    print(f"\nData-quality report: {_rel(path)} ({len(report['warnings'])} warning(s))")
    for warning in report["warnings"][:6]:
        print(f"  ! {warning}")
    if len(report["warnings"]) > 6:
        print(f"  … {len(report['warnings']) - 6} more in the report")

    print("\n" + "-" * 58)
    for label, status in (("streets", network_status), ("entities", entity_status),
                          ("services", service_status)):
        if status:
            print(f"status  {label + ':':<10}{status}")
    failed = FIXTURE_BANNER in {network_status, entity_status, service_status}
    print(f"status  {'overall:':<10}{'READY' if not failed else 'READY (with fixture fallbacks)'}")
    print("-" * 58)
    print("\nStart the app with:  uvicorn app.main:app --reload")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
