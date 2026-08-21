"""One-off data preparation: download, normalize, project, snap and cache.

    python -m app.prepare_data              # everything: networks, entities,
                                            # services, snaps, transit, report
    python -m app.prepare_data --refresh    # ignore caches and re-download
    python -m app.prepare_data --fixture    # write nothing live; report fixture mode
    python -m app.prepare_data --network-only | --services-only
                              | --entities-only | --transit-only

The API server never downloads anything: it loads these caches at startup.

`data/processed/` ships in the repository as a frozen snapshot, so this script
is the *refresh* mechanism, not a prerequisite for running the server. It ends
by comparing what it prepared with the committed snapshot; re-freezing that
snapshot is a separate, deliberate `python -m app.snapshot --write`.
"""
from __future__ import annotations

import argparse
import sys
import traceback

from .config import (
    ENTITY_CACHE,
    POPULATION_CACHE,
    ENTITY_LIMITS,
    GTFS_ARCHIVE,
    MIN_TRANSFER_SECONDS,
    OSMNX_CACHE_DIR,
    SERVICE_CACHE,
    TRANSIT_CACHE,
)
from .data_quality import build_report, write_report
from .errors import BaselGraphError
from .ingest import fetch_entities, load_data, write_entity_cache
from .population import fetch_population as fetch_population_data
from .population import load_population
from .population import write_cache as write_population_cache
from .service_index import ServiceIndex, index_from_payload, snap_services
from .service_model import ESSENTIAL_CATEGORIES, ServiceCategory
from .service_sources import (
    SOURCE_PLAN,
    fetch_services,
    fixture_services,
    network_fingerprints,
    read_cache,
)
from .street_sources import OSMnxNetworkSource, fixture_street_network
from .transit_index import TransitIndex
from .transit_model import Timetable
from .transit_sources import fixture_timetable
from .transit_sources.cache import read_cache as read_transit_cache
from .transit_sources.cache import write_cache as write_transit_cache
from .transit_sources.swiss_gtfs import SwissGTFSTransitSource

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


NETWORK_TITLES = {"walk": "Walking network", "bike": "Cycling network"}


def prepare_network(refresh: bool = False, fixture: bool = False, verbose: bool = True,
                    kind: str = "walk") -> dict:
    print(f"{NETWORK_TITLES.get(kind, kind)}\n")
    if fixture:
        network = fixture_street_network("Fixture mode requested on the command line", kind=kind)
        _print_network(network, cached=None, used_cache=False)
        return {"status": FIXTURE_BANNER, "network": network}

    source = OSMnxNetworkSource(allow_download=True, refresh=refresh, kind=kind)
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
    kind = stats.get("network", "walk")
    print(f"  length:  {stats['total_length_m'] / 1000:,.1f} km of "
          f"{'cyclable' if kind == 'bike' else 'walkable'} ways")
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


def prepare_services(networks, refresh: bool = False, fixture: bool = False) -> dict:
    """Fetch every service category, snap it to every network, cache it."""
    if not isinstance(networks, dict):
        networks = {"walk": networks}
    print("\nServices\n")
    if fixture:
        services = fixture_services()
        _snap_all(networks, services)
        index = ServiceIndex(services, mode="fixture", fallback_reason="Fixture mode requested",
                             networks=tuple(networks))
        _print_services(index, networks, cached=None)
        return {"status": FIXTURE_BANNER, "index": index}

    if SERVICE_CACHE.exists() and not refresh:
        # Read the cache directly: preparation has its own --fixture flag and
        # must not be steered by the server-side BASEL_SERVICE_SOURCE variable.
        try:
            payload = read_cache(SERVICE_CACHE)
            payload["mode"] = "live"
            index = _index_from(payload, networks, resnap_note=True)
            _print_services(index, networks, cached=SERVICE_CACHE, reused=True)
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
        _snap_all(networks, services)
        index = ServiceIndex(services, mode="fixture", networks=tuple(networks),
                             fallback_reason="; ".join(sum(errors.values(), [])) or "no live services")
        _print_services(index, networks, cached=None)
        return {"status": FIXTURE_BANNER, "index": index, "errors": errors}

    _snap_all(networks, services, announce=True)

    from .service_sources import write_cache

    write_cache(services, network_fingerprints(networks), errors=errors)
    index = ServiceIndex(services, mode="live", source_errors=errors, networks=tuple(networks))
    _print_services(index, networks, cached=SERVICE_CACHE)
    for category, messages in errors.items():
        for message in messages:
            print(f"  ! {category}: {message}")
    status = FIXTURE_BANNER if errors and len(errors) == len(SOURCE_PLAN) else LIVE_BANNER
    return {"status": status, "index": index, "errors": errors}


def _snap_all(networks: dict, services, announce: bool = False) -> None:
    """One access point per network, computed once and cached."""
    for name, streets in networks.items():
        if announce:
            print(f"\nService → {name} network attachments")
        snap_services(streets, services, network=name)
        if not announce:
            continue
        accesses = [s.access_for(name) for s in services]
        valid = sum(1 for a in accesses if a.is_routable)
        poor = sum(1 for a in accesses if a.quality == "poor")
        failed = len(accesses) - valid
        print(f"  valid: {valid:,}   poor snaps: {poor:,}   not attached: {failed:,}")


def _index_from(payload, networks, resnap_note: bool = False) -> ServiceIndex:
    """Rebuild the index from a cache, re-snapping and rewriting if it went stale."""
    def note(fingerprints, resnapped):
        if resnap_note:
            print(f"  ! cached snapping is stale for: {', '.join(resnapped)}; re-snapping")
        from .service_sources import write_cache

        write_cache(payload["services"], fingerprints, errors=payload.get("errors"))

    return index_from_payload(payload, networks, on_resnap=note)


def _print_category(category: ServiceCategory, services, errors) -> None:
    plan = "+".join(SOURCE_PLAN.get(category, ()))
    sources = ", ".join(sorted({s.source for s in services})) or "—"
    print(f"  {category.value:<11} {len(services):>5}  via {plan:<8} {sources}")
    for error in errors:
        print(f"    ! {error}")


def _print_services(index: ServiceIndex, networks=None, cached=None, reused: bool = False) -> None:
    for row in index.summary()["categories"]:
        marker = "*" if row["essential"] else " "
        print(f" {marker}{row['label']:<11} {row['count']:>5}  {', '.join(row['sources'])}")
    print(f"\n  total:   {len(index.services):,} service locations")
    for name in (networks or {}):
        routable = sum(1 for s in index.services if s.is_routable_on(name))
        print(f"  attached to {name}: {routable:,}")
    essentials = [c.value for c in ESSENTIAL_CATEGORIES if c not in index.by_category]
    if essentials:
        print(f"  MISSING essential categories: {', '.join(essentials)}")
    if cached is not None:
        print(f"  cached:  {_rel(cached)} ({'reused existing cache' if reused else 'written'})")
    else:
        print("  cached:  not written (fixture data is generated in memory)")


def prepare_population(refresh: bool = False, fixture: bool = False) -> dict:
    """Neighbourhood population by age group — the statistical dimension."""
    print("\nPopulation\n")
    if fixture:
        print("  source:  synthetic fixture (requested)")
        return {"status": FIXTURE_BANNER}
    if POPULATION_CACHE.exists() and not refresh:
        data = load_population()
        if data.get("mode") == "live":
            _print_population(data, reused=True)
            return {"status": LIVE_BANNER}
    try:
        data = fetch_population_data()
    except Exception as exc:
        print(f"  live source FAILED: {exc}")
        print("  The graph will fall back to synthetic population figures.")
        return {"status": FIXTURE_BANNER, "error": str(exc)}
    write_population_cache(data)
    _print_population(data, reused=False)
    return {"status": LIVE_BANNER}


def _print_population(data: dict, reused: bool) -> None:
    provenance = data.get("provenance", {})
    years = data.get("years", [])
    print(f"  source:  {provenance.get('source')}")
    print(f"  dataset: {provenance.get('dataset')} — {provenance.get('dataset_title')}")
    print(f"  unit:    {provenance.get('spatial_unit')}")
    print(f"  years:   {min(years)}–{max(years)} ({len(years)} prepared, latest "
          f"{data.get('latest_year')})")
    print(f"  rows:    {len(data.get('observations', [])):,} observations")
    for name, definition in (provenance.get("age_group_definitions") or {}).items():
        print(f"    {name:<16} {definition}")
    print(f"  cached:  {_rel(POPULATION_CACHE)} "
          f"({'reused existing cache' if reused else 'written'})")


def prepare_transit(streets, refresh: bool = False, fixture: bool = False) -> dict:
    """Download the Swiss GTFS feed, extract the Basel subset, attach it to the
    walking network, and cache the result."""
    print("\nTransit\n")
    if fixture:
        index = TransitIndex(fixture_timetable(), mode="fixture",
                             fallback_reason="Fixture mode requested",
                             min_transfer_seconds=MIN_TRANSFER_SECONDS)
        _attach_stops(index, streets)
        _print_transit(index, cached=None)
        return {"status": FIXTURE_BANNER, "index": index}

    timetable = None
    if TRANSIT_CACHE.exists() and not refresh:
        try:
            timetable = read_transit_cache(TRANSIT_CACHE)
            reused = True
        except BaselGraphError as exc:
            print(f"  cached timetable unusable ({exc.message}); extracting again")
    if timetable is None:
        reused = False
        source = SwissGTFSTransitSource(allow_download=True, refresh=refresh,
                                        progress=lambda message: print(f"  ... {message}", flush=True))
        try:
            records = source.load()
            timetable = Timetable.build(records)
            write_transit_cache(timetable, TRANSIT_CACHE)
        except BaselGraphError as exc:
            print(f"  live timetable FAILED: {exc.message}")
            print("  Falling back to the synthetic fixture timetable.")
            index = TransitIndex(fixture_timetable(), mode="fixture", fallback_reason=exc.message,
                                 min_transfer_seconds=MIN_TRANSFER_SECONDS)
            _attach_stops(index, streets)
            _print_transit(index, cached=None)
            return {"status": FIXTURE_BANNER, "index": index, "error": exc.message}

    index = TransitIndex(timetable, mode="live", min_transfer_seconds=MIN_TRANSFER_SECONDS)
    _attach_stops(index, streets)
    _print_transit(index, cached=TRANSIT_CACHE, reused=reused)
    return {"status": LIVE_BANNER, "index": index}


def _attach_stops(index: TransitIndex, streets) -> None:
    if streets is None or not index.available:
        return
    print("\nStop → walking network attachments")
    index.attach_to_network(streets)
    valid = sum(1 for a in index.stop_access if a.is_routable)
    poor = sum(1 for a in index.stop_access if a.quality == "poor")
    print(f"  valid: {valid:,}   poor snaps: {poor:,}   "
          f"outside the walking network: {len(index.stop_access) - valid:,}")


def _print_transit(index: TransitIndex, cached, reused: bool = False) -> None:
    report = index.quality_report()
    print(f"  source:  {report['source']}")
    if report.get("feed"):
        print(f"  feed:    {report['feed']} ({report.get('feed_version') or 'unversioned'})")
    print(f"  stops:   {report['stops']:,}")
    print(f"  routes:  {report['routes']:,}")
    print(f"  trips:   {report['trips']:,}")
    print(f"  patterns:{report['patterns']:,}")
    dates = report["service_dates"]
    print(f"  service dates: {dates['first']} – {dates['last']} "
          f"({'covers today' if report['serves_today'] else 'DOES NOT cover today'})")
    if report.get("extraction"):
        print(f"  area:    {report['extraction']}")
    if report.get("malformed_records"):
        print(f"  skipped: {report['malformed_records']:,} malformed record(s)")
    if cached is not None:
        print(f"  cached:  {_rel(cached)} ({'reused existing cache' if reused else 'written'})")
        print(f"  archive: {_rel(GTFS_ARCHIVE)}")
    else:
        print("  cached:  not written (fixture data is generated in memory)")
    if index.fallback_reason:
        print(f"  reason:  {index.fallback_reason}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the Basel networks, entities, services and timetable.")
    parser.add_argument("--refresh", action="store_true", help="ignore existing caches and re-download")
    parser.add_argument("--fixture", action="store_true", help="prepare nothing live; report fixture mode")
    parser.add_argument("--network-only", action="store_true", help="only the street networks")
    parser.add_argument("--entities-only", action="store_true", help="only the Basel entity datasets")
    parser.add_argument("--services-only", action="store_true", help="only the service POIs")
    parser.add_argument("--transit-only", action="store_true", help="only the GTFS timetable")
    parser.add_argument("--population-only", action="store_true",
                        help="only the neighbourhood population data")
    parser.add_argument("--skip-spatial-graph", action="store_true",
                        help="do not rebuild the heterogeneous spatial graph at the end")
    args = parser.parse_args(argv)

    only = (args.network_only or args.entities_only or args.services_only
            or args.transit_only or args.population_only)
    do_network = args.network_only or not only
    do_entities = args.entities_only or not only
    do_services = args.services_only or not only
    do_transit = args.transit_only or not only
    do_population = args.population_only or not only

    print("Preparing Basel Spatial Graph...\n")
    statuses = {}
    networks, entities, index, transit = {}, None, None, None

    if do_network or do_services or do_transit:
        for kind in ("walk", "bike"):
            result = prepare_network(refresh=args.refresh, fixture=args.fixture, kind=kind)
            networks[kind] = result["network"]
            if do_network:
                statuses[kind if kind != "walk" else "streets"] = result["status"]
            print()
    if do_entities:
        statuses["entities"] = prepare_entities(refresh=args.refresh, fixture=args.fixture)["status"]
        entities = load_data(force_fixture=args.fixture)
    if do_services:
        result = prepare_services(networks, refresh=args.refresh, fixture=args.fixture)
        statuses["services"] = result["status"]
        index = result["index"]
    if do_transit:
        result = prepare_transit(networks.get("walk"), refresh=args.refresh, fixture=args.fixture)
        statuses["transit"] = result["status"]
        transit = result["index"]
    if do_population:
        statuses["population"] = prepare_population(refresh=args.refresh,
                                                    fixture=args.fixture)["status"]

    report = build_report(networks or None, entities, index, transit)
    path = write_report(report)
    print(f"\nData-quality report: {_rel(path)} ({len(report['warnings'])} warning(s))")
    for warning in report["warnings"][:6]:
        print(f"  ! {warning}")
    if len(report["warnings"]) > 6:
        print(f"  … {len(report['warnings']) - 6} more in the report")

    if not only and not args.skip_spatial_graph:
        from .prepare_spatial_graph import prepare as prepare_graph

        print()
        graph = prepare_graph(fixture=args.fixture, verbose=False)
        statuses["spatial graph"] = (LIVE_BANNER if graph.metadata["mode"] == "live"
                                     else FIXTURE_BANNER)

    from .snapshot import print_check

    print()
    matches = print_check()
    statuses["snapshot"] = ("frozen snapshot (unchanged)" if matches
                            else "local (differs from the committed snapshot)")

    print("\n" + "-" * 58)
    for label in ("streets", "bike", "entities", "services", "transit", "population",
                  "spatial graph", "snapshot"):
        if label in statuses:
            print(f"status  {label + ':':<10}{statuses[label]}")
    failed = FIXTURE_BANNER in statuses.values()
    print(f"status  {'overall:':<10}{'READY' if not failed else 'READY (with fixture fallbacks)'}")
    print("-" * 58)
    print("\nStart the app with:  uvicorn app.main:app --reload")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
