"""The official Swiss GTFS timetable, cut down to a Basel-sized subset.

The national feed is a 224 MB zip whose `stop_times.txt` alone is 2.9 GB
uncompressed, so nothing is ever loaded whole. Every file is streamed straight
out of the archive and filtered as it goes:

    stops.txt         -> stations inside the extraction box
    stop_times.txt    -> trips that touch one of those stations  (one pass)
    trips.txt         -> route and calendar of the kept trips
    routes.txt        -> the routes those trips belong to
    calendar(_dates)  -> only the calendars those trips use
    transfers.txt     -> official minimum interchange times between kept stops

Downloading and extracting only ever happen in `python -m app.prepare_data`.
"""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

import numpy as np

from ..config import (
    GTFS_ARCHIVE,
    GTFS_DATASET_URL,
    GTFS_URL,
    MIN_TRANSFER_SECONDS,
    STOP_TRANSFER_RADIUS_M,
    TRANSIT_BBOX,
)
from ..errors import TransitSourceError
from ..transit_model import (
    GTFSRecords,
    TransitRoute,
    TransitStop,
    TransitTrip,
    parse_gtfs_date,
    parse_gtfs_time,
)
from .base import LIVE, TransitSource, make_transit_provenance, utc_now_iso

LICENSE = "Open data, opentransportdata.swiss (attribution required)"
SOURCE_NAME = "opentransportdata.swiss"


def _rows(archive: zipfile.ZipFile, name: str) -> Iterator[dict]:
    """Stream one CSV member as dicts, without extracting it to disk."""
    try:
        handle = archive.open(name)
    except KeyError:
        return
    with handle as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))


def _fast_stop_times(archive: zipfile.ZipFile) -> Iterator[Tuple[str, str, str, str]]:
    """`stop_times.txt` as (trip_id, arrival, departure, stop_id), quickly.

    40 million rows go past here, so the usual csv machinery is bypassed for
    the common case: every field in this feed is quoted and none contains a
    comma. Any row that does not split cleanly falls back to the csv parser.
    """
    with archive.open("stop_times.txt") as raw:
        stream = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        header = next(stream).rstrip("\r\n").replace('"', "").split(",")
        try:
            trip_at = header.index("trip_id")
            arrive_at = header.index("arrival_time")
            depart_at = header.index("departure_time")
            stop_at = header.index("stop_id")
        except ValueError:
            raise TransitSourceError("stop_times.txt is missing a required column")
        width = len(header)
        for line in stream:
            parts = line.rstrip("\r\n").split(",")
            if len(parts) != width:
                parts = next(csv.reader([line]))
                if len(parts) != width:
                    continue
                yield parts[trip_at], parts[arrive_at], parts[depart_at], parts[stop_at]
                continue
            yield (parts[trip_at].strip('"'), parts[arrive_at].strip('"'),
                   parts[depart_at].strip('"'), parts[stop_at].strip('"'))


class SwissGTFSTransitSource(TransitSource):
    name = SOURCE_NAME

    def __init__(self, archive_path: Path = GTFS_ARCHIVE, bbox=TRANSIT_BBOX,
                 url: str = GTFS_URL, allow_download: bool = False,
                 refresh: bool = False, progress=None):
        self.archive_path = Path(archive_path)
        self.bbox = bbox
        self.url = url
        self.allow_download = allow_download
        self.refresh = refresh
        self.progress = progress or (lambda message: None)
        self.downloaded = False

    # -- acquisition ----------------------------------------------------------
    def ensure_archive(self) -> Path:
        if self.archive_path.exists() and not self.refresh:
            return self.archive_path
        if not self.allow_download:
            raise TransitSourceError(
                f"No GTFS archive at {self.archive_path}. "
                "Run `python -m app.prepare_data` once to download it."
            )
        return self.download()

    def download(self) -> Path:
        import httpx

        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.archive_path.with_suffix(".part")
        self.progress(f"downloading the Swiss GTFS feed from {self.url}")
        try:
            with httpx.stream("GET", self.url, follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                with open(partial, "wb") as out:
                    for chunk in response.iter_bytes(1 << 20):
                        out.write(chunk)
        except Exception as exc:
            partial.unlink(missing_ok=True)
            raise TransitSourceError(f"Could not download the Swiss GTFS feed: {exc}")
        partial.replace(self.archive_path)
        self.downloaded = True
        return self.archive_path

    # -- extraction -----------------------------------------------------------
    def load(self) -> GTFSRecords:
        path = self.ensure_archive()
        try:
            archive = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise TransitSourceError(f"The GTFS archive at {path} is not readable: {exc}")
        with archive:
            return self._extract(archive, path)

    def _extract(self, archive: zipfile.ZipFile, path: Path) -> GTFSRecords:
        malformed = 0
        south, west, north, east = self.bbox

        # 1. stations inside the extraction box -------------------------------
        self.progress("reading stops")
        station_of: Dict[str, str] = {}      # any stop id -> station id
        positions: Dict[str, List[Tuple[float, float]]] = {}
        names: Dict[str, str] = {}
        members: Dict[str, List[str]] = {}
        for row in _rows(archive, "stops.txt"):
            try:
                lat = float(row["stop_lat"])
                lon = float(row["stop_lon"])
            except (TypeError, ValueError, KeyError):
                malformed += 1
                continue
            if not (south <= lat <= north and west <= lon <= east):
                continue
            stop_id = (row.get("stop_id") or "").strip()
            if not stop_id:
                malformed += 1
                continue
            # Platforms collapse into their parent station: passengers change
            # between platforms for free, and it keeps the graph small.
            parent = (row.get("parent_station") or "").strip()
            station = parent or stop_id
            station_of[stop_id] = station
            positions.setdefault(station, []).append((lon, lat))
            members.setdefault(station, []).append(stop_id)
            name = (row.get("stop_name") or "").strip()
            if name and (station == stop_id or station not in names):
                names[station] = name
        if not station_of:
            raise TransitSourceError(
                f"No GTFS stops fall inside the extraction box {self.bbox}")

        station_ids = sorted(positions)
        station_index = {sid: i for i, sid in enumerate(station_ids)}
        self.progress(f"{len(station_ids):,} stations inside the extraction box")

        # 2. one streaming pass over stop_times -------------------------------
        self.progress("scanning stop_times (this is the big one)")
        trip_stops: Dict[str, List[Tuple[str, int, int]]] = {}
        current_trip = None
        buffer: List[Tuple[str, int, int]] = []
        scanned = 0

        def flush(trip_id, rows):
            if trip_id is None or len(rows) < 2:
                return
            trip_stops[trip_id] = rows

        for trip_id, arrive, depart, stop_id in _fast_stop_times(archive):
            scanned += 1
            if trip_id != current_trip:
                flush(current_trip, buffer)
                current_trip, buffer = trip_id, []
            station = station_of.get(stop_id)
            if station is None:
                continue
            arrival = parse_gtfs_time(arrive)
            departure = parse_gtfs_time(depart) if depart else arrival
            if arrival is None or departure is None:
                malformed += 1
                continue
            if buffer and buffer[-1][0] == station:
                continue                       # two platforms of the same station
            if buffer and departure < buffer[-1][2]:
                malformed += 1                 # times must not go backwards
                continue
            buffer.append((station, arrival, departure))
        flush(current_trip, buffer)
        self.progress(f"{scanned:,} stop_times rows scanned, {len(trip_stops):,} local trips kept")

        # 3. what route and calendar those trips belong to ---------------------
        self.progress("reading trips")
        trip_meta: Dict[str, Tuple[str, str, str]] = {}
        for row in _rows(archive, "trips.txt"):
            trip_id = (row.get("trip_id") or "").strip()
            if trip_id not in trip_stops:
                continue
            trip_meta[trip_id] = (
                (row.get("route_id") or "").strip(),
                (row.get("service_id") or "").strip(),
                (row.get("trip_headsign") or "").strip(),
            )
        route_ids = {route for route, _, _ in trip_meta.values() if route}
        service_ids = {service for _, service, _ in trip_meta.values() if service}

        # 4. routes and agencies ----------------------------------------------
        agencies = {
            (row.get("agency_id") or "").strip(): (row.get("agency_name") or "").strip()
            for row in _rows(archive, "agency.txt")
        }
        routes: Dict[str, TransitRoute] = {}
        for row in _rows(archive, "routes.txt"):
            route_id = (row.get("route_id") or "").strip()
            if route_id not in route_ids:
                continue
            agency_id = (row.get("agency_id") or "").strip()
            try:
                route_type = int(row.get("route_type") or 3)
            except ValueError:
                route_type = 3
                malformed += 1
            routes[route_id] = TransitRoute(
                id=route_id,
                short_name=(row.get("route_short_name") or "").strip(),
                long_name=(row.get("route_long_name") or "").strip(),
                route_type=route_type,
                agency_id=agency_id or None,
                agency_name=agencies.get(agency_id) or None,
            )

        # 5. calendars ---------------------------------------------------------
        self.progress("reading calendars")
        calendar: Dict[str, dict] = {}
        for row in _rows(archive, "calendar.txt"):
            service_id = (row.get("service_id") or "").strip()
            if service_id not in service_ids:
                continue
            calendar[service_id] = {
                "days": [1 if (row.get(day) or "0").strip() == "1" else 0 for day in
                         ("monday", "tuesday", "wednesday", "thursday", "friday",
                          "saturday", "sunday")],
                "start": parse_gtfs_date(row.get("start_date")) or 0,
                "end": parse_gtfs_date(row.get("end_date")) or 21001231,
            }
        exceptions: Dict[str, Dict[int, int]] = {}
        for row in _rows(archive, "calendar_dates.txt"):
            service_id = (row.get("service_id") or "").strip()
            if service_id not in service_ids:
                continue
            day = parse_gtfs_date(row.get("date"))
            if day is None:
                malformed += 1
                continue
            try:
                exception = int(row.get("exception_type") or 0)
            except ValueError:
                malformed += 1
                continue
            exceptions.setdefault(service_id, {})[day] = exception

        # 6. assemble ----------------------------------------------------------
        stops = []
        for station in station_ids:
            points = positions[station]
            stops.append(TransitStop(
                id=station,
                name=names.get(station, station),
                lat=float(np.mean([p[1] for p in points])),
                lon=float(np.mean([p[0] for p in points])),
                source_ids=sorted(members.get(station, []))[:8],
            ))
        trips = []
        for trip_id, rows in trip_stops.items():
            meta = trip_meta.get(trip_id)
            if meta is None or meta[0] not in routes:
                continue
            route_id, service_id, headsign = meta
            trips.append(TransitTrip(
                id=trip_id, route_id=route_id, service_id=service_id, headsign=headsign,
                stop_ids=[station for station, _, _ in rows],
                arrivals=[arrival for _, arrival, _ in rows],
                departures=[departure for _, _, departure in rows],
            ))
        if not trips:
            raise TransitSourceError(
                "No usable trips survived the Basel extraction; check the extraction box.")

        transfers = self._transfers(archive, station_of, station_index, stops)
        feed_info = next(iter(_rows(archive, "feed_info.txt")), {})

        self.progress(f"{len(trips):,} trips on {len(routes):,} routes, "
                      f"{len(transfers):,} transfers")
        return GTFSRecords(
            stops=stops, routes=routes, trips=trips, calendar=calendar,
            exceptions=exceptions, transfers=transfers, malformed=malformed,
            meta=make_transit_provenance(
                mode=LIVE,
                source=SOURCE_NAME,
                feed="Swiss national timetable (GTFS 2020)",
                feed_version=feed_info.get("feed_version") or path.name,
                feed_start=feed_info.get("feed_start_date"),
                feed_end=feed_info.get("feed_end_date"),
                source_url=GTFS_DATASET_URL,
                license=LICENSE,
                retrieved_at=utc_now_iso(),
                archive=path.name,
                extraction=(
                    f"stops within lat {self.bbox[0]}–{self.bbox[2]}, "
                    f"lon {self.bbox[1]}–{self.bbox[3]} (Basel and its cross-border surroundings)"
                ),
                publisher=feed_info.get("feed_publisher_name"),
            ),
        )

    def _transfers(self, archive, station_of, station_index, stops) -> Dict[Tuple[str, str], int]:
        """Official interchange times, plus short walks between nearby stops."""
        from ..projection import to_metric

        transfers: Dict[Tuple[str, str], int] = {}
        for row in _rows(archive, "transfers.txt"):
            source = station_of.get((row.get("from_stop_id") or "").strip())
            target = station_of.get((row.get("to_stop_id") or "").strip())
            if not source or not target or source == target:
                continue
            try:
                seconds = int(row.get("min_transfer_time") or MIN_TRANSFER_SECONDS)
            except ValueError:
                seconds = MIN_TRANSFER_SECONDS
            key = (source, target)
            if seconds < transfers.get(key, 10 ** 9):
                transfers[key] = seconds

        # Geometric transfers: stops a short walk apart, at 4.8 km/h.
        xs, ys = to_metric([s.lon for s in stops], [s.lat for s in stops])
        xs = np.atleast_1d(np.asarray(xs, dtype=float))
        ys = np.atleast_1d(np.asarray(ys, dtype=float))
        radius = STOP_TRANSFER_RADIUS_M
        for start in range(0, len(stops), 512):
            block = slice(start, start + 512)
            distances = np.hypot(xs[block, None] - xs[None, :], ys[block, None] - ys[None, :])
            rows, cols = np.nonzero((distances <= radius) & (distances > 0))
            for row_index, col_index in zip(rows, cols):
                a = stops[start + int(row_index)].id
                b = stops[int(col_index)].id
                seconds = int(float(distances[row_index, col_index]) / (4.8 * 1000 / 3600))
                seconds = max(seconds, MIN_TRANSFER_SECONDS)
                if seconds < transfers.get((a, b), 10 ** 9):
                    transfers[(a, b)] = seconds
        return transfers
