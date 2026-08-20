"""Service (POI) sources.

`fetch_services()` downloads and merges providers per category — it is only
called by `python -m app.prepare_data`. `load_services()` is what the server
uses: it reads the prepared cache, or degrades to the fixture with a reason.

Which provider serves which category is a single, readable table; no category
is tied to one provider.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

from ..errors import BaselGraphError, ServiceSourceError
from ..service_model import ServiceCategory, ServiceLocation
from .base import ServiceSource, dedupe, duplicate_candidates
from .basel_open_data import BaselOpenDataServiceSource
from .cache import network_fingerprint, network_fingerprints, read_cache, write_cache
from .fixture_source import FixtureServiceSource, fixture_services
from .osm_source import OSMServiceSource

__all__ = [
    "BaselOpenDataServiceSource",
    "FixtureServiceSource",
    "OSMServiceSource",
    "ServiceSource",
    "SOURCE_PLAN",
    "duplicate_candidates",
    "fetch_services",
    "fixture_services",
    "load_services",
    "network_fingerprint",
    "network_fingerprints",
    "read_cache",
    "write_cache",
]

SOURCE_ENV = "BASEL_SERVICE_SOURCE"  # auto | fixture

_PROVIDERS = {
    "bs": BaselOpenDataServiceSource,
    "osm": OSMServiceSource,
    "fixture": FixtureServiceSource,
}

# Official Basel-Stadt data wherever the canton publishes it; OpenStreetMap for
# the rest. Healthcare deliberately merges both: the canton lists clinics and
# hospitals, OSM lists the doctors' practices.
SOURCE_PLAN: Dict[ServiceCategory, Sequence[str]] = {
    ServiceCategory.SCHOOL: ("bs",),
    ServiceCategory.SPORT: ("bs",),
    ServiceCategory.CULTURE: ("bs",),
    ServiceCategory.HEALTHCARE: ("bs", "osm"),
    ServiceCategory.GROCERY: ("osm",),
    ServiceCategory.PHARMACY: ("osm",),
    ServiceCategory.PARK: ("osm",),
    ServiceCategory.LIBRARY: ("osm",),
}

PREPARED_CATEGORIES = tuple(SOURCE_PLAN)


def fetch_services(categories: Optional[Sequence[ServiceCategory]] = None, on_progress=None):
    """Download every configured category. Returns (services, errors_by_category).

    A category that fails does not abort the others — the failure is recorded
    and reported, and that category is simply absent rather than faked.
    """
    wanted = tuple(categories or PREPARED_CATEGORIES)
    services: List[ServiceLocation] = []
    errors: Dict[str, list] = {}
    cache: Dict[str, ServiceSource] = {}
    for category in wanted:
        collected: List[ServiceLocation] = []
        for key in SOURCE_PLAN.get(category, ()):
            source = cache.setdefault(key, _PROVIDERS[key]())
            try:
                collected.extend(source.fetch(category))
            except BaselGraphError as exc:
                errors.setdefault(category.value, []).append(f"{key}: {exc.message}")
            except Exception as exc:
                errors.setdefault(category.value, []).append(f"{key}: {exc}")
        collected = dedupe(collected)
        services.extend(collected)
        if on_progress:
            on_progress(category, collected, errors.get(category.value, []))
    return services, errors


def load_services(force_fixture: bool = False, path=None, source: Optional[str] = None):
    """Server-side load: cache only, never a live request on startup.

    Returns a dict with `services`, `mode`, `fallback_reason` and cache metadata.
    """
    source = (source or os.getenv(SOURCE_ENV, "auto")).lower()
    if force_fixture or source == "fixture":
        return {
            "services": fixture_services(),
            "mode": "fixture",
            "fallback_reason": "Fixture mode requested",
            "network_fingerprints": {},
            "generated_at": None,
            "errors": {},
        }
    try:
        payload = read_cache(path)
        payload["mode"] = "live"
        payload["fallback_reason"] = None
        return payload
    except ServiceSourceError as exc:
        return {
            "services": fixture_services(),
            "mode": "fixture",
            "fallback_reason": exc.message,
            "network_fingerprints": {},
            "generated_at": None,
            "errors": {},
        }
    except Exception as exc:
        return {
            "services": fixture_services(),
            "mode": "fixture",
            "fallback_reason": str(exc),
            "network_fingerprints": {},
            "generated_at": None,
            "errors": {},
        }
