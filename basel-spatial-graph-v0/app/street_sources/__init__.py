"""Walking-network sources.

`load_street_network()` is the only entry point the rest of the app uses; it
resolves a source, and if the preferred one is unavailable it degrades to the
fixture with an explicit, reported reason. It never downloads.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ..config import WALK_NETWORK_CACHE
from ..errors import BaselGraphError
from .base import FIXTURE, LIVE, StreetNetwork, WalkingNetworkSource, make_provenance
from .fixture_source import FixtureWalkingNetworkSource, fixture_street_network
from .graphml_cache import read_cache, write_cache
from .osmnx_source import OSMnxWalkingNetworkSource

__all__ = [
    "FIXTURE",
    "LIVE",
    "FixtureWalkingNetworkSource",
    "OSMnxWalkingNetworkSource",
    "StreetNetwork",
    "WalkingNetworkSource",
    "fixture_street_network",
    "load_street_network",
    "make_provenance",
    "read_cache",
    "write_cache",
]

SOURCE_ENV = "BASEL_STREET_NETWORK_SOURCE"  # auto | osmnx | fixture


def load_street_network(
    force_fixture: bool = False,
    cache_path: Optional[Path] = None,
    source: Optional[str] = None,
) -> StreetNetwork:
    """Load the walking network for the running application."""
    source = (source or os.getenv(SOURCE_ENV, "auto")).lower()
    if force_fixture or source == "fixture":
        return fixture_street_network("Fixture mode requested")
    try:
        return OSMnxWalkingNetworkSource(
            cache_path=cache_path or WALK_NETWORK_CACHE, allow_download=False
        ).load()
    except BaselGraphError as exc:
        if source == "osmnx":
            raise
        return fixture_street_network(exc.message)
    except Exception as exc:  # unexpected cache problem must still start the app
        if source == "osmnx":
            raise
        return fixture_street_network(str(exc))
