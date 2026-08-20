"""Contract every public-transport source implements.

A source returns `GTFSRecords` — normalized stops, routes, trips, calendars and
transfers — plus provenance. Nothing downstream knows whether that came from the
official Swiss feed or from a hand-written fixture.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

from ..transit_model import GTFSRecords

LIVE = "live"
FIXTURE = "fixture"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_transit_provenance(
    *,
    mode: str,
    source: str,
    feed: str,
    feed_version: Optional[str] = None,
    feed_start: Optional[str] = None,
    feed_end: Optional[str] = None,
    source_url: Optional[str] = None,
    license: Optional[str] = None,
    retrieved_at: Optional[str] = None,
    **extra,
) -> dict:
    """One provenance shape for every timetable source."""
    return {
        "mode": mode,
        "fixture": mode == FIXTURE,
        "source": source,
        "feed": feed,
        "feed_version": feed_version,
        "feed_start_date": feed_start,
        "feed_end_date": feed_end,
        "source_url": source_url,
        "license": license,
        "retrieved_at": retrieved_at,
        "timezone": "Europe/Zurich",
        **extra,
    }


class TransitSource(ABC):
    """Supplies a normalized timetable for the prepared area."""

    name: str = "unknown"

    @abstractmethod
    def load(self) -> GTFSRecords:
        """Return normalized records, or raise `TransitSourceError`."""
