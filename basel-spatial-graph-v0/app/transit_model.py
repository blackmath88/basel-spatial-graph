"""Normalized public-transport timetable and the structures routing needs.

GTFS is a pile of CSV files; this module turns the parts we need into a compact,
array-backed timetable that a schedule-aware search can scan quickly.

The domain concepts stay recognisable — `TransitStop`, `TransitRoute`,
`TransitTrip` — but the routing representation groups trips into *patterns*
(all trips that visit exactly the same stop sequence). That is what makes a
round-based search cheap, and it is an implementation detail: nothing outside
this module and `transit_index` needs to know about patterns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from datetime import timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

DAY_SECONDS = 86400
INF = np.int32(2 ** 31 - 1)

# GTFS route_type -> a human label. The 1xx values are the extended set the
# Swiss feed uses (109 = suburban railway, 900 = tram, ...).
ROUTE_TYPE_LABELS = {
    0: "Tram", 1: "Metro", 2: "Train", 3: "Bus", 4: "Ferry", 5: "Cable tram",
    6: "Aerial lift", 7: "Funicular", 11: "Trolleybus", 12: "Monorail",
    100: "Train", 102: "Train", 103: "Train", 106: "Train", 109: "S-Bahn",
    400: "Metro", 700: "Bus", 704: "Bus", 715: "On-demand bus", 900: "Tram",
    1000: "Ferry", 1300: "Aerial lift", 1400: "Funicular", 1501: "Shared taxi",
}


def route_type_label(route_type) -> str:
    try:
        return ROUTE_TYPE_LABELS.get(int(route_type), "Transit")
    except (TypeError, ValueError):
        return "Transit"


def parse_gtfs_time(value) -> Optional[int]:
    """`HH:MM:SS` -> seconds after that service day's midnight.

    GTFS hours may exceed 23: `25:15:00` is 01:15 on the following calendar day
    but still belongs to the previous service day. That is the whole reason this
    function exists instead of `datetime.strptime`.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(p) for p in parts)
    except ValueError:
        return None
    if minutes < 0 or minutes > 59 or seconds < 0 or seconds > 59 or hours < 0:
        return None
    return hours * 3600 + minutes * 60 + seconds


def format_gtfs_time(seconds: Optional[int]) -> Optional[str]:
    """Inverse of `parse_gtfs_time`, keeping hours past 24 intact."""
    if seconds is None:
        return None
    seconds = int(seconds)
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    return f"{sign}{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def parse_gtfs_date(value) -> Optional[int]:
    """`YYYYMMDD` -> int, the form GTFS calendars use."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if 19000101 <= number <= 21001231 else None


def date_to_int(day: Date) -> int:
    return day.year * 10000 + day.month * 100 + day.day


@dataclass
class TransitStop:
    id: str
    name: str
    lat: float
    lon: float
    source_ids: List[str] = field(default_factory=list)


@dataclass
class TransitRoute:
    id: str
    short_name: str
    long_name: str
    route_type: int
    agency_id: Optional[str] = None
    agency_name: Optional[str] = None

    @property
    def label(self) -> str:
        """What a passenger would call it: `Tram 8`, `Bus 30`, `S-Bahn S3`."""
        kind = route_type_label(self.route_type)
        name = self.short_name or self.long_name
        return f"{kind} {name}".strip() if name else kind


@dataclass
class TransitTrip:
    """One scheduled run. Times are seconds after its service day's midnight."""

    id: str
    route_id: str
    service_id: str
    headsign: str
    stop_ids: Sequence[str]
    arrivals: Sequence[int]
    departures: Sequence[int]


@dataclass
class GTFSRecords:
    """The normalized hand-off between a GTFS source and the timetable builder."""

    stops: List[TransitStop] = field(default_factory=list)
    routes: Dict[str, TransitRoute] = field(default_factory=dict)
    trips: List[TransitTrip] = field(default_factory=list)
    calendar: Dict[str, dict] = field(default_factory=dict)
    exceptions: Dict[str, Dict[int, int]] = field(default_factory=dict)
    transfers: Dict[Tuple[str, str], int] = field(default_factory=dict)
    meta: Dict = field(default_factory=dict)
    malformed: int = 0


class Timetable:
    """Array-backed timetable: patterns, trips, calendars and foot transfers."""

    def __init__(self, **arrays):
        self.__dict__.update(arrays)
        self._day_cache: Dict[int, "DayView"] = {}
        self.stop_index = {sid: i for i, sid in enumerate(self.stop_ids)}

    # -- construction ---------------------------------------------------------
    @classmethod
    def build(cls, records: GTFSRecords) -> "Timetable":
        stop_ids = [s.id for s in records.stops]
        stop_index = {sid: i for i, sid in enumerate(stop_ids)}

        route_ids = list(records.routes)
        route_index = {rid: i for i, rid in enumerate(route_ids)}
        service_ids = sorted({t.service_id for t in records.trips} | set(records.calendar))
        service_index = {sid: i for i, sid in enumerate(service_ids)}

        # Group trips into patterns: same route, same exact stop sequence.
        patterns: Dict[tuple, List[TransitTrip]] = {}
        for trip in records.trips:
            sequence = tuple(stop_index[s] for s in trip.stop_ids if s in stop_index)
            if len(sequence) < 2 or len(sequence) != len(trip.stop_ids):
                continue
            patterns.setdefault((trip.route_id, sequence), []).append(trip)

        pattern_stops, pattern_offsets = [], [0]
        pattern_route, pattern_trip_start, pattern_trip_count = [], [], []
        pattern_time_start = []
        trip_ids, trip_headsigns, trip_service = [], [], []
        arrivals, departures = [], []

        for (route_id, sequence), trips in patterns.items():
            trips.sort(key=lambda t: t.departures[0])
            pattern_stops.extend(sequence)
            pattern_offsets.append(len(pattern_stops))
            pattern_route.append(route_index.get(route_id, -1))
            pattern_trip_start.append(len(trip_ids))
            pattern_trip_count.append(len(trips))
            pattern_time_start.append(len(arrivals))
            for trip in trips:
                trip_ids.append(trip.id)
                trip_headsigns.append(trip.headsign or "")
                trip_service.append(service_index.get(trip.service_id, -1))
                arrivals.extend(int(v) for v in trip.arrivals)
                departures.extend(int(v) for v in trip.departures)

        # Foot transfers between stops, as a CSR adjacency.
        transfer_targets: List[List[Tuple[int, int]]] = [[] for _ in stop_ids]
        for (from_id, to_id), seconds in records.transfers.items():
            a, b = stop_index.get(from_id), stop_index.get(to_id)
            if a is None or b is None or a == b:
                continue
            transfer_targets[a].append((b, int(seconds)))
        flat_targets, flat_seconds, transfer_offsets = [], [], [0]
        for entries in transfer_targets:
            for target, seconds in entries:
                flat_targets.append(target)
                flat_seconds.append(seconds)
            transfer_offsets.append(len(flat_targets))

        calendar_days = np.zeros((len(service_ids), 7), dtype=np.int8)
        calendar_start = np.zeros(len(service_ids), dtype=np.int32)
        calendar_end = np.zeros(len(service_ids), dtype=np.int32)
        for sid, entry in records.calendar.items():
            i = service_index.get(sid)
            if i is None:
                continue
            calendar_days[i] = entry.get("days", [0] * 7)
            calendar_start[i] = entry.get("start") or 0
            calendar_end[i] = entry.get("end") or 21001231

        return cls(
            stop_ids=stop_ids,
            stop_names=[s.name for s in records.stops],
            stop_lat=np.array([s.lat for s in records.stops], dtype=float),
            stop_lon=np.array([s.lon for s in records.stops], dtype=float),
            stop_source_ids=[list(s.source_ids) for s in records.stops],
            routes=[records.routes[r] for r in route_ids],
            pattern_stops=np.array(pattern_stops, dtype=np.int32),
            pattern_offsets=np.array(pattern_offsets, dtype=np.int64),
            pattern_route=np.array(pattern_route, dtype=np.int32),
            pattern_trip_start=np.array(pattern_trip_start, dtype=np.int64),
            pattern_trip_count=np.array(pattern_trip_count, dtype=np.int64),
            pattern_time_start=np.array(pattern_time_start, dtype=np.int64),
            trip_ids=trip_ids,
            trip_headsigns=trip_headsigns,
            trip_service=np.array(trip_service, dtype=np.int32),
            arrivals=np.array(arrivals, dtype=np.int32),
            departures=np.array(departures, dtype=np.int32),
            service_ids=service_ids,
            calendar_days=calendar_days,
            calendar_start=calendar_start,
            calendar_end=calendar_end,
            exceptions={k: {int(d): int(v) for d, v in dates.items()}
                        for k, dates in records.exceptions.items()},
            transfer_offsets=np.array(transfer_offsets, dtype=np.int64),
            transfer_targets=np.array(flat_targets, dtype=np.int32),
            transfer_seconds=np.array(flat_seconds, dtype=np.int32),
            meta=dict(records.meta),
            malformed=records.malformed,
        )

    # -- introspection --------------------------------------------------------
    @property
    def stop_count(self) -> int:
        return len(self.stop_ids)

    @property
    def route_count(self) -> int:
        return len(self.routes)

    @property
    def trip_count(self) -> int:
        return len(self.trip_ids)

    @property
    def pattern_count(self) -> int:
        return len(self.pattern_route)

    def pattern_stop_slice(self, pattern: int) -> np.ndarray:
        return self.pattern_stops[self.pattern_offsets[pattern]:self.pattern_offsets[pattern + 1]]

    def service_window(self) -> Tuple[Optional[int], Optional[int]]:
        """The first and last calendar date the timetable says anything about."""
        starts = [int(v) for v in self.calendar_start if v]
        ends = [int(v) for v in self.calendar_end if v]
        for dates in self.exceptions.values():
            starts.extend(dates)
            ends.extend(dates)
        return (min(starts) if starts else None, max(ends) if ends else None)

    def active_services(self, day: Date) -> np.ndarray:
        """Boolean mask of services running on `day`, honouring exceptions."""
        key = date_to_int(day)
        weekday = day.weekday()
        active = (
            (self.calendar_days[:, weekday] == 1)
            & (self.calendar_start <= key)
            & (self.calendar_end >= key)
        )
        for service_id, dates in self.exceptions.items():
            exception = dates.get(key)
            if exception is None:
                continue
            index = self.service_ids.index(service_id) if service_id in self.service_ids else None
            if index is None:
                continue
            active[index] = exception == 1
        return active

    def serves(self, day: Date) -> bool:
        return bool(self.active_services(day).any())

    # -- per-day view ---------------------------------------------------------
    def day_view(self, day: Date) -> "DayView":
        """Trips usable on `day`, on a timeline of seconds after its midnight.

        Trips from the *previous* service day whose times run past 24:00 are
        included with a -86400 s offset, which is how a 25:15 departure becomes
        01:15 this morning.
        """
        key = date_to_int(day)
        if key not in self._day_cache:
            if len(self._day_cache) > 8:
                self._day_cache.clear()
            self._day_cache[key] = DayView(self, day)
        return self._day_cache[key]


class DayView:
    """One service day, materialized so the search can binary-search departures."""

    def __init__(self, timetable: Timetable, day: Date):
        self.timetable = timetable
        self.day = day
        today = timetable.active_services(day)
        yesterday = timetable.active_services(day - timedelta(days=1))

        offsets = np.full(timetable.trip_count, INF, dtype=np.int64)
        service = timetable.trip_service
        valid = service >= 0
        offsets[valid & today[np.clip(service, 0, None)]] = 0

        # A yesterday-trip only matters if it is still running after midnight.
        starts = timetable.pattern_time_start
        counts = timetable.pattern_trip_count
        trip_starts = timetable.pattern_trip_start
        lengths = np.diff(timetable.pattern_offsets)

        arrivals, departures, trip_index, order_start, order_count = [], [], [], [], []
        for pattern in range(timetable.pattern_count):
            length = int(lengths[pattern])
            count = int(counts[pattern])
            if count == 0 or length == 0:
                order_start.append(len(trip_index))
                order_count.append(0)
                continue
            block = slice(int(starts[pattern]), int(starts[pattern]) + count * length)
            arr = timetable.arrivals[block].reshape(count, length)
            dep = timetable.departures[block].reshape(count, length)
            trips = np.arange(int(trip_starts[pattern]), int(trip_starts[pattern]) + count)
            trip_services = service[trips]

            runs_today = today[np.clip(trip_services, 0, None)] & (trip_services >= 0)
            ran_yesterday = (
                yesterday[np.clip(trip_services, 0, None)] & (trip_services >= 0)
                & (arr[:, -1] >= DAY_SECONDS)
            )
            if not runs_today.any() and not ran_yesterday.any():
                order_start.append(len(trip_index))
                order_count.append(0)
                continue

            pieces = []
            if ran_yesterday.any():
                pieces.append((arr[ran_yesterday] - DAY_SECONDS,
                               dep[ran_yesterday] - DAY_SECONDS,
                               trips[ran_yesterday]))
            if runs_today.any():
                pieces.append((arr[runs_today], dep[runs_today], trips[runs_today]))
            day_arr = np.concatenate([p[0] for p in pieces])
            day_dep = np.concatenate([p[1] for p in pieces])
            day_trips = np.concatenate([p[2] for p in pieces])
            # Re-sort: an after-midnight run from yesterday departs before the
            # first trip of today, and the search relies on this order.
            order = np.argsort(day_dep[:, 0], kind="stable")

            order_start.append(len(trip_index))
            order_count.append(len(order))
            arrivals.append(day_arr[order])
            departures.append(day_dep[order])
            trip_index.extend(day_trips[order].tolist())

        self.pattern_trip_start = np.array(order_start, dtype=np.int64)
        self.pattern_trip_count = np.array(order_count, dtype=np.int64)
        self.trip_index = np.array(trip_index, dtype=np.int64)
        self.arrivals = arrivals    # list of (n_trips, length) arrays per pattern
        self.departures = departures
        self._pattern_slot = {}
        slot = 0
        for pattern, count in enumerate(order_count):
            if count:
                self._pattern_slot[pattern] = slot
                slot += 1

    def pattern_times(self, pattern: int):
        """(arrivals, departures, global trip indices) for one pattern, or None."""
        slot = self._pattern_slot.get(pattern)
        if slot is None:
            return None
        start = int(self.pattern_trip_start[pattern])
        count = int(self.pattern_trip_count[pattern])
        return (self.arrivals[slot], self.departures[slot],
                self.trip_index[start:start + count])

    @property
    def active_trip_count(self) -> int:
        return int(self.pattern_trip_count.sum())
