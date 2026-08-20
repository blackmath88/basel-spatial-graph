"""Schedule-aware transit search, and how stops attach to the walking network.

The search is RAPTOR (Round-bAsed Public Transit Optimized Router): round *k*
holds the earliest arrival at every stop using at most *k* vehicles, so limiting
transfers is simply running fewer rounds. It is exact, easy to read and fast
enough to answer interactively without any database.

Waiting is never assumed away. A round boards the earliest departure at or after
the moment the passenger is actually ready at that stop, so the gap between
arriving at a stop and the vehicle leaving is real time spent.
"""
from __future__ import annotations

from datetime import date as Date
from typing import Dict, List, Optional

import numpy as np

from .config import MAX_SERVICE_SNAP_M, POOR_SERVICE_SNAP_M
from .transit_model import Timetable, route_type_label

INF = 10 ** 9
# Changing vehicles at the same stop needs a moment on the platform. Walking
# transfers carry their own time instead, so this is not added to those.
DEFAULT_MIN_TRANSFER_SECONDS = 90


class StopAccess:
    """How one transit stop attaches to the pedestrian network."""

    __slots__ = ("node_id", "distance_m", "quality")

    def __init__(self, node_id=None, distance_m=None, quality="unsnapped"):
        self.node_id = node_id
        self.distance_m = distance_m
        self.quality = quality

    @property
    def is_routable(self) -> bool:
        return self.node_id is not None and self.quality in {"good", "poor"}

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "snap_distance_m": round(self.distance_m, 1) if self.distance_m is not None else None,
            "quality": self.quality,
        }


class SearchResult:
    """Earliest arrival per stop, plus the labels needed to explain a journey."""

    def __init__(self, arrival, per_round, board, transfer_from, best_round,
                 start_seconds, day):
        self.arrival = arrival
        self.per_round = per_round
        self.board = board
        self.transfer_from = transfer_from
        self.best_round = best_round
        self.start_seconds = start_seconds
        self.day = day

    def reached_stops(self) -> Dict[int, int]:
        return {int(i): int(t) for i, t in enumerate(self.arrival) if t < INF}

    def transfers_to(self, stop: int) -> int:
        """Vehicles used minus one; 0 for a single ride."""
        return max(0, int(self.best_round[int(stop)]) - 1)


class TransitIndex:
    """A prepared timetable plus its attachment to the walking network."""

    def __init__(self, timetable: Optional[Timetable], provenance: Optional[dict] = None,
                 mode: str = "fixture", fallback_reason: Optional[str] = None,
                 min_transfer_seconds: int = DEFAULT_MIN_TRANSFER_SECONDS):
        self.timetable = timetable
        self.provenance = dict(provenance or (timetable.meta if timetable else {}))
        self.mode = mode
        self.fallback_reason = fallback_reason
        self.min_transfer_seconds = min_transfer_seconds
        self.stop_access: List[StopAccess] = []
        self.access_map: Dict[str, List[int]] = {}
        self.network_fingerprint: Optional[str] = None
        self._stop_patterns: Optional[List[List[tuple]]] = None
        if timetable is not None:
            self.stop_access = [StopAccess() for _ in range(timetable.stop_count)]

    # -- availability ---------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.timetable is not None and self.timetable.trip_count > 0

    @property
    def stop_patterns(self) -> List[List[tuple]]:
        """stop -> [(pattern, position)], built once and reused."""
        if self._stop_patterns is None:
            table = self.timetable
            index: List[List[tuple]] = [[] for _ in range(table.stop_count)]
            for pattern in range(table.pattern_count):
                for position, stop in enumerate(table.pattern_stop_slice(pattern)):
                    index[int(stop)].append((pattern, position))
            self._stop_patterns = index
        return self._stop_patterns

    # -- attachment -----------------------------------------------------------
    def attach_to_network(self, streets, poor_m: float = POOR_SERVICE_SNAP_M,
                          max_m: float = MAX_SERVICE_SNAP_M) -> "TransitIndex":
        """Snap every stop to its nearest pedestrian node, once."""
        if not self.available:
            return self
        table = self.timetable
        points = list(zip(table.stop_lon.tolist(), table.stop_lat.tolist()))
        snapped = streets.nearest_nodes(points)
        self.stop_access = []
        self.access_map = {}
        for index, (node_id, distance) in enumerate(snapped):
            if distance > max_m:
                access = StopAccess(None, distance, "unreachable")
            elif distance > poor_m:
                access = StopAccess(node_id, distance, "poor")
            else:
                access = StopAccess(node_id, distance, "good")
            self.stop_access.append(access)
            if access.is_routable:
                self.access_map.setdefault(access.node_id, []).append(index)
        return self

    # -- the search -----------------------------------------------------------
    def search(self, initial: Dict[int, int], start_seconds: int, limit_seconds: int,
               day: Date, max_transfers: int = 1) -> SearchResult:
        """RAPTOR from stops already reached on foot.

        `initial` maps stop index -> the second (on `day`'s timeline) at which
        the passenger is standing at that stop. `limit_seconds` is the arrival
        time beyond which nothing counts any more. Round *k* holds the earliest
        arrival using at most *k* vehicles, so `max_transfers` is just a round
        count.
        """
        table = self.timetable
        count = table.stop_count
        rounds = max(1, int(max_transfers) + 1)
        day_view = table.day_view(day)

        best = np.full(count, INF, dtype=np.int64)
        # per_round[k][stop]: earliest arrival with at most k vehicles.
        # ready[k][stop]: when a further vehicle may be boarded there.
        per_round = [np.full(count, INF, dtype=np.int64) for _ in range(rounds + 1)]
        ready = [np.full(count, INF, dtype=np.int64) for _ in range(rounds + 1)]
        board: List[Dict[int, tuple]] = [{} for _ in range(rounds + 1)]
        transfer_from: List[Dict[int, tuple]] = [{} for _ in range(rounds + 1)]

        marked = set()
        for stop, seconds in initial.items():
            stop = int(stop)
            if seconds <= limit_seconds and seconds < per_round[0][stop]:
                per_round[0][stop] = ready[0][stop] = best[stop] = int(seconds)
                marked.add(stop)
        marked |= self._relax_transfers(marked, per_round[0], ready[0], best,
                                        transfer_from[0], limit_seconds)

        for number in range(1, rounds + 1):
            per_round[number] = per_round[number - 1].copy()
            ready[number] = ready[number - 1].copy()
            previous_ready = ready[number - 1]

            queue: Dict[int, int] = {}
            for stop in marked:
                for pattern, position in self.stop_patterns[stop]:
                    if position < queue.get(pattern, 1 << 30):
                        queue[pattern] = position
            marked = set()

            for pattern, first_position in queue.items():
                times = day_view.pattern_times(pattern)
                if times is None:
                    continue
                arrivals, departures, trip_rows = times
                stops = table.pattern_stop_slice(pattern)
                trip = -1
                board_position = -1
                for position in range(first_position, len(stops)):
                    stop = int(stops[position])
                    if trip >= 0:
                        reached = int(arrivals[trip, position])
                        if reached <= limit_seconds and reached < best[stop]:
                            best[stop] = per_round[number][stop] = reached
                            ready[number][stop] = reached + self.min_transfer_seconds
                            board[number][stop] = (pattern, int(trip_rows[trip]), int(trip),
                                                   board_position, int(stops[board_position]))
                            transfer_from[number].pop(stop, None)
                            marked.add(stop)
                    at = int(previous_ready[stop])
                    if at >= INF:
                        continue
                    candidate = int(np.searchsorted(departures[:, position], at, side="left"))
                    if candidate < departures.shape[0] and (trip < 0 or candidate < trip):
                        trip = candidate
                        board_position = position

            marked |= self._relax_transfers(marked, per_round[number], ready[number], best,
                                            transfer_from[number], limit_seconds)
            if not marked:
                break

        best_round = np.zeros(count, dtype=np.int32)
        for number in range(rounds, -1, -1):
            reached = (per_round[number] == best) & (best < INF)
            best_round[reached] = number
        return SearchResult(best, per_round, board, transfer_from, best_round,
                            start_seconds, day)

    def _relax_transfers(self, marked, round_arrival, round_ready, best,
                         transfer_from, limit_seconds) -> set:
        """Walk between nearby stops. Footpath time replaces the platform wait."""
        table = self.timetable
        improved = set()
        for stop in list(marked):
            base = int(round_arrival[stop])
            if base >= INF:
                continue
            for slot in range(int(table.transfer_offsets[stop]),
                              int(table.transfer_offsets[stop + 1])):
                target = int(table.transfer_targets[slot])
                seconds = int(table.transfer_seconds[slot])
                candidate = base + seconds
                if candidate > limit_seconds or candidate >= best[target]:
                    continue
                best[target] = round_arrival[target] = candidate
                round_ready[target] = candidate
                transfer_from[target] = (stop, seconds)
                improved.add(target)
        return improved

    # -- explaining a journey -------------------------------------------------
    def journey_legs(self, result: SearchResult, stop: int) -> List[dict]:
        """Reconstruct the ride and transfer legs that reached `stop`, in order."""
        table = self.timetable
        day_view = table.day_view(result.day)
        legs: List[dict] = []
        current = int(stop)
        number = int(result.best_round[current])
        guard = 0
        while number > 0 and guard < 64:
            guard += 1
            entry = result.board[number].get(current)
            if current in result.transfer_from[number] and entry is None:
                source, seconds = result.transfer_from[number][current]
                legs.append({
                    "kind": "transfer",
                    "from_stop": self.stop_summary(source),
                    "to_stop": self.stop_summary(current),
                    "seconds": int(seconds),
                })
                current = source
                continue
            if entry is None:
                number -= 1
                continue
            pattern, trip_index, trip_row, board_position, board_stop = entry
            times = day_view.pattern_times(pattern)
            arrivals, departures, _ = times
            stops = table.pattern_stop_slice(pattern)
            exit_position = int(np.where(stops == current)[0][0]) if (stops == current).any() else board_position
            legs.append({
                "kind": "ride",
                "route": self.route_summary(pattern),
                "trip_id": table.trip_ids[trip_index],
                "headsign": table.trip_headsigns[trip_index],
                "from_stop": self.stop_summary(board_stop),
                "to_stop": self.stop_summary(current),
                "departure_seconds": int(departures[trip_row, board_position]),
                "arrival_seconds": int(arrivals[trip_row, exit_position]),
                "stop_count": max(1, exit_position - board_position),
            })
            current = int(board_stop)
            number -= 1
        legs.reverse()
        return legs

    def stop_summary(self, index: int) -> dict:
        table = self.timetable
        access = self.stop_access[index] if index < len(self.stop_access) else StopAccess()
        return {
            "id": table.stop_ids[index],
            "name": table.stop_names[index],
            "lat": round(float(table.stop_lat[index]), 6),
            "lon": round(float(table.stop_lon[index]), 6),
            "access": access.to_dict(),
        }

    def route_summary_for_index(self, route_index: int) -> dict:
        """Describe a route by its own index, not by a pattern that uses it."""
        route = self.timetable.routes[int(route_index)]
        return {
            "id": route.id,
            "short_name": route.short_name,
            "long_name": route.long_name,
            "route_type": route.route_type,
            "vehicle": route_type_label(route.route_type),
            "label": route.label,
            "agency_id": route.agency_id,
            "agency": route.agency_name,
        }

    def route_summary(self, pattern: int) -> dict:
        table = self.timetable
        route = table.routes[int(table.pattern_route[pattern])]
        return {
            "id": route.id,
            "short_name": route.short_name,
            "long_name": route.long_name,
            "route_type": route.route_type,
            "vehicle": route_type_label(route.route_type),
            "label": route.label,
            "agency_id": route.agency_id,
            "agency": route.agency_name,
        }

    # -- reporting ------------------------------------------------------------
    def quality_report(self) -> dict:
        table = self.timetable
        distances = [a.distance_m for a in self.stop_access if a.distance_m is not None]
        window = table.service_window() if table else (None, None)
        from datetime import date as _Date

        return {
            "mode": self.mode,
            "source": self.provenance.get("source"),
            "feed": self.provenance.get("feed"),
            "feed_version": self.provenance.get("feed_version"),
            "retrieved_at": self.provenance.get("retrieved_at"),
            "extraction": self.provenance.get("extraction"),
            "fallback_reason": self.fallback_reason,
            "stops": table.stop_count if table else 0,
            "routes": table.route_count if table else 0,
            "trips": table.trip_count if table else 0,
            "patterns": table.pattern_count if table else 0,
            "service_dates": {"first": window[0], "last": window[1]},
            "serves_today": bool(table and table.serves(_Date.today())),
            "malformed_records": getattr(table, "malformed", 0) if table else 0,
            "stop_snap_failures": sum(1 for a in self.stop_access if not a.is_routable),
            "poor_stop_snaps": sum(1 for a in self.stop_access if a.quality == "poor"),
            "median_stop_snap_m": round(float(np.median(distances)), 1) if distances else None,
            "max_stop_snap_m": round(float(max(distances)), 1) if distances else None,
            "min_transfer_seconds": self.min_transfer_seconds,
        }
