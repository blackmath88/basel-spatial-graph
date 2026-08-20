"""A tiny, hand-written timetable used by every automated transit test.

Four stops on the synthetic walking grid, four routes, three service calendars
and one calendar exception — enough to exercise waiting, riding, transferring,
weekday/weekend calendars and an after-midnight departure without ever touching
the 224 MB Swiss feed.

    stopA ──T1 tram 8──> stopB ──T1──> stopC          (weekdays, every 10 min)
    stopB ──T2 bus 30──> stopD                        (weekdays)
    stopA ──W1 tram 2──> stopD                        (weekends only)
    stopA ──N1 night bus──> stopC   at 25:05          (daily, after midnight)
"""
from __future__ import annotations

from ..transit_model import GTFSRecords, TransitRoute, TransitStop, TransitTrip, parse_gtfs_time
from .base import FIXTURE, TransitSource, make_transit_provenance

SOURCE_NAME = "synthetic fixture"
RETRIEVED_AT = "2020-01-01T00:00:00+00:00"   # fixed, so fixtures stay byte-stable

# Placed exactly on nodes of the synthetic walking grid.
STOPS = [
    TransitStop("stopA", "Fixture A", 47.550, 7.574, ["stopA:1"]),
    TransitStop("stopB", "Fixture B", 47.550, 7.592, ["stopB:1"]),
    TransitStop("stopC", "Fixture C", 47.558, 7.604, ["stopC:1"]),
    TransitStop("stopD", "Fixture D", 47.562, 7.604, ["stopD:1"]),
]

ROUTES = {
    "T1": TransitRoute("T1", "8", "Fixture tram line", 900, "FX", "Fixture Transit"),
    "T2": TransitRoute("T2", "30", "Fixture bus line", 700, "FX", "Fixture Transit"),
    "W1": TransitRoute("W1", "2", "Fixture weekend tram", 900, "FX", "Fixture Transit"),
    "N1": TransitRoute("N1", "N1", "Fixture night bus", 700, "FX", "Fixture Transit"),
}

CALENDAR = {
    "WKDY": {"days": [1, 1, 1, 1, 1, 0, 0], "start": 20260101, "end": 20261231},
    "WKND": {"days": [0, 0, 0, 0, 0, 1, 1], "start": 20260101, "end": 20261231},
    "DAILY": {"days": [1, 1, 1, 1, 1, 1, 1], "start": 20260101, "end": 20261231},
}
# 1 July 2026 is a Wednesday running a weekend timetable.
EXCEPTIONS = {"WKDY": {20260701: 2}, "WKND": {20260701: 1}}

# Stops C and D are ~450 m apart: a foot transfer, not a ride.
TRANSFERS = {("stopC", "stopD"): 330, ("stopD", "stopC"): 330}


def _trip(trip_id, route_id, service_id, headsign, legs) -> TransitTrip:
    """`legs` is [(stop_id, arrival, departure)] as GTFS time strings."""
    return TransitTrip(
        id=trip_id, route_id=route_id, service_id=service_id, headsign=headsign,
        stop_ids=[stop for stop, _, _ in legs],
        arrivals=[parse_gtfs_time(arrival) for _, arrival, _ in legs],
        departures=[parse_gtfs_time(departure) for _, _, departure in legs],
    )


def _timed(trip_id, route_id, service_id, headsign, first_departure, legs) -> TransitTrip:
    """`legs` is [(stop_id, minutes after the first departure)]."""
    base = parse_gtfs_time(first_departure)
    seconds = [base + minutes * 60 for _, minutes in legs]
    return TransitTrip(
        id=trip_id, route_id=route_id, service_id=service_id, headsign=headsign,
        stop_ids=[stop for stop, _ in legs],
        arrivals=list(seconds), departures=list(seconds),
    )


def _t1_trips():
    """Tram 8 A→B→C every ten minutes from 09:45 to 10:35."""
    return [
        _timed(f"T1-{index}", "T1", "WKDY", "Fixture C", "09:45:00",
               [("stopA", index * 10), ("stopB", index * 10 + 4), ("stopC", index * 10 + 8)])
        for index in range(6)
    ]


TRIPS = _t1_trips() + [
    # Bus 30 B→D, timed for a 3-minute transfer off the 10:05 tram.
    _timed("T2-0", "T2", "WKDY", "Fixture D", "10:12:00", [("stopB", 0), ("stopD", 4)]),
    _timed("T2-1", "T2", "WKDY", "Fixture D", "10:32:00", [("stopB", 0), ("stopD", 4)]),
    # Weekend-only direct tram A→D.
    _timed("W1-0", "W1", "WKND", "Fixture D", "10:05:00", [("stopA", 0), ("stopD", 6)]),
    # Night bus leaving at 25:05 — 01:05 the next morning, previous service day.
    _timed("N1-0", "N1", "DAILY", "Fixture C", "25:05:00", [("stopA", 0), ("stopC", 8)]),
]


class FixtureTransitSource(TransitSource):
    name = SOURCE_NAME

    def load(self) -> GTFSRecords:
        return GTFSRecords(
            stops=list(STOPS),
            routes=dict(ROUTES),
            trips=list(TRIPS),
            calendar={k: dict(v) for k, v in CALENDAR.items()},
            exceptions={k: dict(v) for k, v in EXCEPTIONS.items()},
            transfers=dict(TRANSFERS),
            meta=make_transit_provenance(
                mode=FIXTURE,
                source=SOURCE_NAME,
                feed="Synthetic Basel-centred timetable",
                feed_version="fixture-1",
                feed_start="20260101",
                feed_end="20261231",
                license="fixture-only; not real observations",
                retrieved_at=RETRIEVED_AT,
                extraction="the four synthetic stops on the fixture walking grid",
            ),
        )


def fixture_transit_records() -> GTFSRecords:
    return FixtureTransitSource().load()
