"""Public-transport sources.

`fetch_transit()` downloads and extracts — only `python -m app.prepare_data`
calls it. `load_transit()` is what the server uses: it reads the prepared
timetable cache, or degrades to the fixture with a stated reason.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ..config import MIN_TRANSFER_SECONDS
from ..errors import BaselGraphError
from ..transit_index import TransitIndex
from ..transit_model import Timetable
from .base import FIXTURE, LIVE, TransitSource
from .cache import read_cache, write_cache
from .fixture_source import FixtureTransitSource, fixture_transit_records
from .swiss_gtfs import SwissGTFSTransitSource

__all__ = [
    "FixtureTransitSource",
    "SwissGTFSTransitSource",
    "TransitSource",
    "fixture_timetable",
    "load_transit",
    "read_cache",
    "write_cache",
]

SOURCE_ENV = "BASEL_TRANSIT_SOURCE"  # auto | gtfs | fixture


def fixture_timetable() -> Timetable:
    return Timetable.build(fixture_transit_records())


def load_transit(force_fixture: bool = False, path: Optional[Path] = None,
                 source: Optional[str] = None,
                 min_transfer_seconds: int = MIN_TRANSFER_SECONDS) -> TransitIndex:
    """Load the prepared timetable. Never downloads, never re-extracts."""
    source = (source or os.getenv(SOURCE_ENV, "auto")).lower()
    if force_fixture or source == "fixture":
        return TransitIndex(fixture_timetable(), mode=FIXTURE,
                            fallback_reason="Fixture mode requested",
                            min_transfer_seconds=min_transfer_seconds)
    try:
        timetable = read_cache(path)
        return TransitIndex(timetable, mode=LIVE, min_transfer_seconds=min_transfer_seconds)
    except BaselGraphError as exc:
        if source == "gtfs":
            raise
        return TransitIndex(fixture_timetable(), mode=FIXTURE, fallback_reason=exc.message,
                            min_transfer_seconds=min_transfer_seconds)
    except Exception as exc:
        if source == "gtfs":
            raise
        return TransitIndex(fixture_timetable(), mode=FIXTURE, fallback_reason=str(exc),
                            min_transfer_seconds=min_transfer_seconds)
