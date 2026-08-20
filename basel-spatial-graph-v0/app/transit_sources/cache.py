"""The prepared timetable cache: `data/processed/basel_transit.npz`.

A built `Timetable` is mostly numeric arrays, so it is stored as a compressed
`.npz` with the id tables, calendars and provenance carried alongside as one
JSON blob. Loading is a few array reads rather than re-parsing GTFS, which is
what keeps server startup at roughly a second.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import TRANSIT_CACHE
from ..errors import TransitSourceError
from ..transit_model import Timetable, TransitRoute

FORMAT_VERSION = 1

ARRAY_FIELDS = (
    "stop_lat", "stop_lon", "pattern_stops", "pattern_offsets", "pattern_route",
    "pattern_trip_start", "pattern_trip_count", "pattern_time_start", "trip_service",
    "arrivals", "departures", "calendar_days", "calendar_start", "calendar_end",
    "transfer_offsets", "transfer_targets", "transfer_seconds",
)


def write_cache(timetable: Timetable, path=None, network_fingerprint: Optional[str] = None) -> Path:
    path = Path(path or TRANSIT_CACHE)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "format_version": FORMAT_VERSION,
        "stop_ids": timetable.stop_ids,
        "stop_names": timetable.stop_names,
        "stop_source_ids": timetable.stop_source_ids,
        "routes": [
            {
                "id": r.id, "short_name": r.short_name, "long_name": r.long_name,
                "route_type": r.route_type, "agency_id": r.agency_id,
                "agency_name": r.agency_name,
            }
            for r in timetable.routes
        ],
        "trip_ids": timetable.trip_ids,
        "trip_headsigns": timetable.trip_headsigns,
        "service_ids": timetable.service_ids,
        "exceptions": {k: {str(d): v for d, v in dates.items()}
                       for k, dates in timetable.exceptions.items()},
        "provenance": timetable.meta,
        "malformed": getattr(timetable, "malformed", 0),
        "network_fingerprint": network_fingerprint,
    }
    arrays = {name: getattr(timetable, name) for name in ARRAY_FIELDS}
    np.savez_compressed(path, meta=np.array(json.dumps(meta)), **arrays)
    return path


def read_cache(path=None) -> Timetable:
    path = Path(path or TRANSIT_CACHE)
    if not path.exists():
        raise TransitSourceError(
            f"No prepared timetable at {path}. Run `python -m app.prepare_data`."
        )
    try:
        with np.load(path, allow_pickle=False) as blob:
            meta = json.loads(str(blob["meta"]))
            arrays = {name: blob[name] for name in ARRAY_FIELDS}
    except Exception as exc:
        raise TransitSourceError(f"The prepared timetable at {path} is unreadable: {exc}")
    if meta.get("format_version") != FORMAT_VERSION:
        raise TransitSourceError(
            f"The prepared timetable at {path} was written by another version "
            f"({meta.get('format_version')} != {FORMAT_VERSION}). Re-run `python -m app.prepare_data`."
        )

    timetable = Timetable(
        stop_ids=meta["stop_ids"],
        stop_names=meta["stop_names"],
        stop_source_ids=meta.get("stop_source_ids", []),
        routes=[TransitRoute(**row) for row in meta["routes"]],
        trip_ids=meta["trip_ids"],
        trip_headsigns=meta["trip_headsigns"],
        service_ids=meta["service_ids"],
        exceptions={k: {int(d): int(v) for d, v in dates.items()}
                    for k, dates in meta.get("exceptions", {}).items()},
        meta=meta.get("provenance", {}),
        malformed=meta.get("malformed", 0),
        cache_path=str(path),
        network_fingerprint=meta.get("network_fingerprint"),
        **arrays,
    )
    if timetable.trip_count == 0:
        raise TransitSourceError(f"The prepared timetable at {path} contains no trips")
    return timetable
