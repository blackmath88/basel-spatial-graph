"""Walking-network sources.

`load_street_network()` is the only entry point the rest of the app uses; it
resolves a source, and if the preferred one is unavailable it degrades to the
fixture with an explicit, reported reason. It never downloads.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ..config import NETWORK_CACHES, WALK_NETWORK_CACHE
from ..errors import BaselGraphError
from .base import FIXTURE, LIVE, StreetNetwork, WalkingNetworkSource, make_provenance
from .fixture_source import (
    FixtureCyclingNetworkSource,
    FixtureWalkingNetworkSource,
    fixture_street_network,
)
from .graphml_cache import read_cache, write_cache
from .osmnx_source import (
    OSMnxCyclingNetworkSource,
    OSMnxNetworkSource,
    OSMnxWalkingNetworkSource,
)

__all__ = [
    "FIXTURE",
    "LIVE",
    "FixtureCyclingNetworkSource",
    "FixtureWalkingNetworkSource",
    "OSMnxCyclingNetworkSource",
    "OSMnxNetworkSource",
    "OSMnxWalkingNetworkSource",
    "StreetNetwork",
    "WalkingNetworkSource",
    "fixture_street_network",
    "load_network",
    "load_street_network",
    "make_provenance",
    "read_cache",
    "write_cache",
]

SOURCE_ENV = "BASEL_STREET_NETWORK_SOURCE"  # auto | osmnx | fixture


def load_network(
    kind: str = "walk",
    force_fixture: bool = False,
    cache_path: Optional[Path] = None,
    source: Optional[str] = None,
) -> StreetNetwork:
    """Load one prepared street network (`walk` or `bike`) for the application.

    Never downloads. If the prepared cache is missing it degrades to the
    fixture grid and says why, unless the source was pinned to `osmnx`.
    """
    source = (source or os.getenv(SOURCE_ENV, "auto")).lower()
    if force_fixture or source == "fixture":
        return fixture_street_network("Fixture mode requested", kind=kind)
    try:
        return OSMnxNetworkSource(
            cache_path=cache_path or NETWORK_CACHES.get(kind, WALK_NETWORK_CACHE),
            allow_download=False, kind=kind,
        ).load()
    except BaselGraphError as exc:
        if source == "osmnx":
            raise
        return fixture_street_network(exc.message, kind=kind)
    except Exception as exc:  # unexpected cache problem must still start the app
        if source == "osmnx":
            raise
        return fixture_street_network(str(exc), kind=kind)


def load_street_network(
    force_fixture: bool = False,
    cache_path: Optional[Path] = None,
    source: Optional[str] = None,
) -> StreetNetwork:
    """The pedestrian network. Kept as its own name for the V0.2 call sites."""
    return load_network("walk", force_fixture, cache_path, source)
