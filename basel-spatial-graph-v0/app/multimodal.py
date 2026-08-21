"""Walk → Transit → Walk accessibility.

The shape of the answer is the same as walking or cycling — categories, counts,
nearest times, completeness — but the cost of reaching a destination is now

    walk to a stop  +  wait for the vehicle  +  ride  +  (transfer)  +  walk

and therefore depends on *when* you leave. Nothing here assumes zero waiting.

Three phases, all cheap:

1. Dijkstra on the pedestrian graph from the origin gives the walking time to
   every node, and so to every stop that has one.
2. RAPTOR (`transit_index`) turns those into arrival times at every stop the
   timetable can reach inside the budget.
3. A second, multi-source Dijkstra seeded at the origin *and* at every reached
   stop gives the earliest arrival at every pedestrian node — which is exactly
   what the reachable-services lookup needs. Walking-only destinations fall out
   of the same pass, because the origin is one of the sources.
"""
from __future__ import annotations

import heapq
from datetime import date as Date
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import networkx as nx
from shapely.geometry import LineString, mapping

from .config import DEFAULT_WALKING_SPEED_KMH
from .errors import (
    EmptyNetworkError,
    InvalidDepartureError,
    TransitUnavailableError,
    UnknownServiceError,
    UnroutableServiceError,
)
from .modes import TravelMode, mode_label
from .service_index import ServiceIndex
from .service_model import ServiceCategory
from .transit_index import INF, TransitIndex
from .transit_model import format_gtfs_time

try:                                   # Python 3.9+
    from zoneinfo import ZoneInfo
    ZURICH = ZoneInfo("Europe/Zurich")
except Exception:                      # pragma: no cover - no tz database
    ZURICH = None

DEFAULT_MAX_TRANSFERS = 1
MAX_TRANSFERS_LIMIT = 3


def _date_from_int(value: int) -> Date:
    return Date(value // 10000, value // 100 % 100, value % 100)


def now_in_zurich() -> datetime:
    return datetime.now(ZURICH) if ZURICH else datetime.now()


def parse_departure(value, reference: Optional[datetime] = None) -> datetime:
    """Accept `2026-08-20T14:30`, `14:30`, or nothing (meaning now).

    Always returned in Europe/Zurich, because that is the timezone the Swiss
    timetable is written in.
    """
    reference = reference or now_in_zurich()
    if value in (None, "", "now"):
        return reference
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=ZURICH)
    text = str(value).strip().replace("Z", "+00:00")
    for parse in (
        lambda t: datetime.fromisoformat(t),
        lambda t: datetime.combine(reference.date(), datetime.strptime(t, "%H:%M").time()),
        lambda t: datetime.combine(reference.date(), datetime.strptime(t, "%H:%M:%S").time()),
    ):
        try:
            moment = parse(text)
        except ValueError:
            continue
        return moment.replace(tzinfo=ZURICH) if moment.tzinfo is None else moment.astimezone(ZURICH)
    raise InvalidDepartureError(
        f"Could not read departure time '{value}'. Use HH:MM or an ISO datetime.",
        departure_time=str(value),
    )


class MultimodalAccessibilityService:
    """Answers 'what can I reach by walking and public transport from here?'."""

    travel_mode = TravelMode.TRANSIT

    def __init__(self, streets, transit: TransitIndex,
                 entity_graph: Optional[nx.MultiDiGraph] = None,
                 services: Optional[ServiceIndex] = None,
                 default_speed_kmh: float = DEFAULT_WALKING_SPEED_KMH):
        self.streets = streets
        self.transit = transit
        self.entity_graph = entity_graph if entity_graph is not None else nx.MultiDiGraph()
        self.services = services if services is not None else ServiceIndex([])
        self.default_speed_kmh = default_speed_kmh
        self.mode = self.travel_mode.value
        self.network = "walk"

    @property
    def label(self) -> str:
        return mode_label(self.travel_mode)

    @property
    def available(self) -> bool:
        return self.transit is not None and self.transit.available

    # -- helpers --------------------------------------------------------------
    def _require_transit(self):
        if not self.available:
            raise TransitUnavailableError(
                "No timetable is prepared. Run `python -m app.prepare_data` to add one.",
                reason=self.transit.fallback_reason if self.transit else None,
            )

    def resolve_day(self, departure: datetime) -> Tuple[Date, bool, Optional[str]]:
        """Use the requested date, or the nearest one the feed actually covers.

        A timetable covers roughly a year. Asking about a date outside it is a
        normal thing to do — the answer names the date it actually used rather
        than pretending or refusing.
        """
        table = self.transit.timetable
        requested = departure.date()
        if table.serves(requested):
            return requested, True, None

        def substitute(day: Date) -> Tuple[Date, bool, str]:
            return day, False, (
                f"The prepared timetable does not cover {requested.isoformat()}; "
                f"using {day.isoformat()} instead."
            )

        for offset in range(1, 15):
            for candidate in (requested + timedelta(days=offset), requested - timedelta(days=offset)):
                if table.serves(candidate):
                    return substitute(candidate)

        # Far outside the feed: walk in from whichever edge of its window is nearer.
        first, last = table.service_window()
        if first is None or last is None:
            raise TransitUnavailableError("The prepared timetable has no service calendar at all.")
        edge = _date_from_int(first) if requested < _date_from_int(first) else _date_from_int(last)
        step = 1 if requested < _date_from_int(first) else -1
        for offset in range(0, 400):
            candidate = edge + timedelta(days=offset * step)
            if table.serves(candidate):
                return substitute(candidate)
        raise TransitUnavailableError(
            f"The prepared timetable covers no usable service date "
            f"(feed window {first}–{last}).",
        )

    @staticmethod
    def _seconds_of_day(moment: datetime, day: Date) -> int:
        midnight = datetime.combine(day, datetime.min.time())
        if moment.tzinfo and ZURICH:
            midnight = midnight.replace(tzinfo=ZURICH)
        elif moment.tzinfo:
            midnight = midnight.replace(tzinfo=moment.tzinfo)
        return int((moment - midnight).total_seconds())

    def _walk_seconds_per_metre(self, speed_kmh: float) -> float:
        return 3600.0 / (speed_kmh * 1000.0)

    def _dijkstra_seconds(self, sources: Dict[str, float], limit: float,
                          seconds_per_metre: float):
        """Multi-source Dijkstra in seconds; returns (cost, source-of-label)."""
        graph = self.streets.graph
        cost: Dict[str, float] = {}
        origin_of: Dict[str, str] = {}
        heap = []
        for node, start in sources.items():
            if node in graph and start <= limit and start < cost.get(node, INF):
                cost[node] = start
                origin_of[node] = node
                heapq.heappush(heap, (start, node, node))
        while heap:
            spent, node, source = heapq.heappop(heap)
            if spent > cost.get(node, INF):
                continue
            for neighbour, data in graph[node].items():
                candidate = spent + data["length_m"] * seconds_per_metre
                if candidate <= limit and candidate < cost.get(neighbour, INF):
                    cost[neighbour] = candidate
                    origin_of[neighbour] = source
                    heapq.heappush(heap, (candidate, neighbour, source))
        return cost, origin_of

    # -- the query ------------------------------------------------------------
    def calculate(self, lat: float, lon: float, minutes: float = 15.0,
                  departure_time=None, max_transfers: int = DEFAULT_MAX_TRANSFERS,
                  walking_speed_kmh: Optional[float] = None,
                  categories: Optional[Sequence[ServiceCategory]] = None,
                  include_services: bool = True, include_service_items: bool = True,
                  service_limit: Optional[int] = None,
                  include_geometry: bool = True,
                  journey_limit: int = 12) -> dict:
        self._require_transit()
        if self.streets.graph.number_of_nodes() == 0:
            raise EmptyNetworkError("The walking network contains no nodes.")
        minutes = _positive(minutes, "minutes")
        speed_kmh = _positive(walking_speed_kmh or self.default_speed_kmh, "walking_speed_kmh")
        max_transfers = max(0, min(int(max_transfers), MAX_TRANSFERS_LIMIT))

        departure = parse_departure(departure_time)
        day, exact_date, date_note = self.resolve_day(departure)
        start = self._seconds_of_day(departure, day)
        budget = minutes * 60.0
        limit = start + budget
        per_metre = self._walk_seconds_per_metre(speed_kmh)

        origin_node, snap_m = self.streets.nearest_node(lat, lon)
        origin_seconds = snap_m * per_metre

        # 1. how long it takes to walk to every stop
        walk_only, _ = self._dijkstra_seconds({origin_node: origin_seconds}, budget, per_metre)
        initial: Dict[int, int] = {}
        for node, seconds in walk_only.items():
            for stop in self.transit.access_map.get(node, ()):
                access = self.transit.stop_access[stop]
                at = seconds + access.distance_m * per_metre
                if at <= budget and at < initial.get(stop, INF):
                    initial[stop] = at
        boarding_seconds = {stop: start + value for stop, value in initial.items()}

        # 2. ride the timetable
        result = self.transit.search(boarding_seconds, start, int(limit), day,
                                     max_transfers=max_transfers)

        # 3. walk onwards from wherever transit got us — and from the origin
        sources: Dict[str, float] = {origin_node: origin_seconds}
        stop_of_node: Dict[str, int] = {}
        for stop, arrival in result.reached_stops().items():
            access = self.transit.stop_access[stop]
            if not access.is_routable:
                continue
            elapsed = (arrival - start) + access.distance_m * per_metre
            if elapsed > budget:
                continue
            if elapsed < sources.get(access.node_id, INF):
                sources[access.node_id] = elapsed
                stop_of_node[access.node_id] = stop
        elapsed_cost, origin_of = self._dijkstra_seconds(sources, budget, per_metre)

        reachable_services = {}
        if include_services:
            reachable_services = self._reachable_services(
                elapsed_cost, origin_of, stop_of_node, result, budget, per_metre,
                categories, include_service_items, service_limit, start, boarding_seconds,
                journey_limit,
            )
        completeness = ServiceIndex.completeness(reachable_services) if include_services else None

        notes = []
        if date_note:
            notes.append(date_note)
        if not initial:
            notes.append("No transit stop is within walking range of this origin in the time budget.")
        elif len(result.reached_stops()) <= len(initial):
            notes.append("No departure leaves early enough to get anywhere new within the budget.")

        payload = {
            "origin": {"lat": round(float(lat), 6), "lon": round(float(lon), 6)},
            "snapped_origin": {
                "node_id": origin_node,
                "lat": round(float(self.streets.graph.nodes[origin_node]["lat"]), 6),
                "lon": round(float(self.streets.graph.nodes[origin_node]["lon"]), 6),
                "snap_distance_m": round(snap_m, 1),
            },
            "mode": self.mode,
            "mode_label": self.label,
            "minutes": minutes,
            "walking_speed_kmh": speed_kmh,
            "speed_kmh": speed_kmh,
            "departure_time": departure.isoformat(timespec="minutes"),
            "service_date": day.isoformat(),
            "service_date_is_requested_date": exact_date,
            "max_transfers": max_transfers,
            "network": {
                "origin_node_id": origin_node,
                "reachable_node_count": len(elapsed_cost),
                "walk_only_node_count": len(walk_only),
                "distance_budget_m": round(budget / per_metre, 1),
                "time_budget_seconds": int(budget),
            },
            "transit": {
                "stops_in_walking_range": len(initial),
                "stops_reached": len(result.reached_stops()),
                "stops_reached_by_vehicle": sum(
                    1 for stop in result.reached_stops() if int(result.best_round[stop]) > 0),
                "routes_used": self._routes_used(result),
                "min_transfer_seconds": self.transit.min_transfer_seconds,
            },
            "reachable_services": reachable_services,
            "completeness": completeness,
            "notes": notes,
            "provenance": self._provenance(day, departure, speed_kmh, minutes, max_transfers),
        }
        if include_geometry:
            payload["geometry"] = self._geometry(walk_only, result, start)
        else:
            payload["geometry"] = {"type": "FeatureCollection", "features": []}
        return payload

    # -- pieces ---------------------------------------------------------------
    def _reachable_services(self, elapsed_cost, origin_of, stop_of_node, result, budget,
                            per_metre, categories, include_items, limit, start,
                            boarding_seconds, journey_limit=12):
        wanted = tuple(categories) if categories else tuple(self.services.categories)
        wanted_set = set(wanted)
        found: Dict[ServiceCategory, List] = {c: [] for c in wanted}
        access_map = self.services.access_maps.get("walk", {})
        for node, seconds in elapsed_cost.items():
            for service in access_map.get(node, ()):
                if service.category not in wanted_set:
                    continue
                access = service.access_for("walk")
                total = seconds + access.distance_m * per_metre
                if total <= budget:
                    found[service.category].append((total, service, node))

        result_rows = {}
        for category in wanted:
            rows = sorted(found[category], key=lambda item: item[0])
            selected = rows[:limit] if limit else rows
            items = []
            if include_items:
                for position, (total, service, node) in enumerate(selected):
                    summary = self.services._item(service, 0, 1, "walk")
                    summary.pop("travel_distance_m", None)
                    summary.pop("walking_distance_m", None)
                    summary["travel_time_minutes"] = round(total / 60.0, 1)
                    summary["walking_time_minutes"] = summary["travel_time_minutes"]
                    # Reconstructing an itinerary is cheap but not free, and a
                    # 30-minute transit query can reach a thousand destinations.
                    # The nearest few carry theirs; the rest are one click away
                    # via /accessibility/transit/route.
                    summary["journey"] = (
                        self._journey_summary(node, total, origin_of, stop_of_node,
                                              result, start, boarding_seconds)
                        if position < journey_limit else None
                    )
                    items.append(summary)
            nearest = rows[0] if rows else None
            result_rows[category.value] = self.services.category_row(
                category, len(rows),
                nearest_seconds=nearest[0] if nearest else None,
                nearest_service=nearest[1] if nearest else None,
                items=items,
                ids=[service.id for _, service, _ in rows],
                truncated=bool(limit and len(rows) > limit),
            )
        return result_rows

    def _journey_summary(self, node, total_seconds, origin_of, stop_of_node, result,
                         start, boarding_seconds) -> dict:
        """The walk / wait / ride breakdown behind one reachable destination.

        Everything is derived from times we already computed, so the parts
        always add up to the total the profile reports.
        """
        source_node = origin_of.get(node)
        exit_stop = stop_of_node.get(source_node)
        if exit_stop is None:                       # walked the whole way
            return {
                "uses_transit": False,
                "total_minutes": round(total_seconds / 60.0, 1),
                "walking_minutes": round(total_seconds / 60.0, 1),
                "waiting_minutes": 0.0,
                "transit_minutes": 0.0,
                "transfers": 0,
                "routes": [],
                "legs": [],
                "steps": [{"kind": "walk", "minutes": round(total_seconds / 60.0, 1)}],
            }

        legs = self.transit.journey_legs(result, exit_stop)
        rides = [leg for leg in legs if leg["kind"] == "ride"]
        if not rides:                               # reached only by foot transfers
            return {
                "uses_transit": False,
                "total_minutes": round(total_seconds / 60.0, 1),
                "walking_minutes": round(total_seconds / 60.0, 1),
                "waiting_minutes": 0.0,
                "transit_minutes": 0.0,
                "transfers": 0,
                "routes": [],
                "legs": legs,
                "steps": [{"kind": "walk", "minutes": round(total_seconds / 60.0, 1)}],
            }

        board_stop_index = self._stop_index(rides[0]["from_stop"]["id"])
        board_ready = boarding_seconds.get(board_stop_index, start)
        walk_to_stop = max(0.0, board_ready - start)
        ride_seconds = sum(leg["arrival_seconds"] - leg["departure_seconds"] for leg in rides)
        transfer_walk = sum(leg["seconds"] for leg in legs if leg["kind"] == "transfer")
        elapsed_at_exit = int(result.arrival[exit_stop]) - start
        final_walk = max(0.0, total_seconds - elapsed_at_exit)
        waiting = max(0.0, elapsed_at_exit - walk_to_stop - ride_seconds - transfer_walk)

        return {
            "uses_transit": True,
            "total_minutes": round(total_seconds / 60.0, 1),
            "walking_minutes": round((walk_to_stop + transfer_walk + final_walk) / 60.0, 1),
            "waiting_minutes": round(waiting / 60.0, 1),
            "transit_minutes": round(ride_seconds / 60.0, 1),
            "transfers": max(0, len(rides) - 1),
            "boarding_stop": rides[0]["from_stop"],
            "exit_stop": rides[-1]["to_stop"],
            "routes": [leg["route"]["label"] for leg in rides],
            "walk_to_stop_minutes": round(walk_to_stop / 60.0, 1),
            "final_walk_minutes": round(final_walk / 60.0, 1),
            "legs": legs,
            "steps": self._itinerary_steps(legs, board_ready, final_walk, walk_to_stop),
        }

    def _stop_index(self, stop_id) -> Optional[int]:
        table = self.transit.timetable
        return table.stop_index.get(stop_id)

    def _itinerary_steps(self, legs, board_ready, final_walk, walk_to_stop) -> List[dict]:
        """A human-readable sequence: walk → board → wait → ride → exit → walk."""
        steps: List[dict] = [{"kind": "walk", "minutes": round(walk_to_stop / 60.0, 1),
                              "detail": "to the stop"}]
        ready = board_ready
        for leg in legs:
            if leg["kind"] == "transfer":
                steps.append({"kind": "transfer_walk", "minutes": round(leg["seconds"] / 60.0, 1),
                              "from": leg["from_stop"]["name"], "to": leg["to_stop"]["name"]})
                ready = ready + leg["seconds"]
                continue
            wait = max(0.0, leg["departure_seconds"] - ready)
            steps.append({"kind": "board", "stop": leg["from_stop"]["name"],
                          "departure": format_gtfs_time(leg["departure_seconds"]),
                          "route": leg["route"]["label"], "headsign": leg["headsign"]})
            steps.append({"kind": "wait", "minutes": round(wait / 60.0, 1)})
            steps.append({
                "kind": "ride",
                "route": leg["route"]["label"],
                "minutes": round((leg["arrival_seconds"] - leg["departure_seconds"]) / 60.0, 1),
                "stops": leg["stop_count"],
            })
            steps.append({"kind": "exit", "stop": leg["to_stop"]["name"],
                          "arrival": format_gtfs_time(leg["arrival_seconds"])})
            ready = leg["arrival_seconds"] + self.transit.min_transfer_seconds
        steps.append({"kind": "walk", "minutes": round(final_walk / 60.0, 1),
                      "detail": "to the destination"})
        return steps

    def _routes_used(self, result) -> List[dict]:
        seen, routes = set(), []
        for number in range(len(result.board)):
            for entry in result.board[number].values():
                pattern = entry[0]
                summary = self.transit.route_summary(pattern)
                if summary["id"] in seen:
                    continue
                seen.add(summary["id"])
                routes.append(summary)
        return sorted(routes, key=lambda r: (r["vehicle"], r["short_name"]))

    def _geometry(self, walk_only, result, start):
        """What the map should show: the walk from the origin, the rides taken,
        and the stops reached.

        Deliberately *not* the full walkable envelope around every reached stop:
        at 30 minutes that is most of Basel, several megabytes of GeoJSON, and a
        blue blob that says nothing. The service profile still counts all of it.
        """
        graph = self.streets.graph
        features = []
        seen = set()
        for node in walk_only:
            for neighbour, data in graph[node].items():
                if neighbour not in walk_only:
                    continue
                key = (node, neighbour) if str(node) <= str(neighbour) else (neighbour, node)
                if key in seen:
                    continue
                seen.add(key)
                feature = self.streets.edge_feature(node, neighbour, data)
                feature["properties"] = {"kind": "reachable_edge",
                                         "length_m": feature["properties"]["length_m"]}
                features.append(feature)
        for pattern, board_stop, exit_stop in self._used_segments(result):
            table = self.transit.timetable
            features.append({
                "type": "Feature",
                "geometry": _round_geometry(mapping(LineString([
                    (float(table.stop_lon[board_stop]), float(table.stop_lat[board_stop])),
                    (float(table.stop_lon[exit_stop]), float(table.stop_lat[exit_stop])),
                ]))),
                "properties": {
                    "kind": "transit_segment",
                    "route": self.transit.route_summary(pattern)["label"],
                    "vehicle": self.transit.route_summary(pattern)["vehicle"],
                },
            })
        for stop, arrival in result.reached_stops().items():
            if int(result.best_round[stop]) == 0:
                continue
            summary = self.transit.stop_summary(stop)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [summary["lon"], summary["lat"]]},
                "properties": {
                    "kind": "transit_stop",
                    "name": summary["name"],
                    "arrival": format_gtfs_time(arrival),
                    "minutes": round((arrival - start) / 60.0, 1),
                },
            })
        return {"type": "FeatureCollection", "features": features}

    def _used_segments(self, result):
        segments = set()
        table = self.transit.timetable
        for number in range(len(result.board)):
            for stop, entry in result.board[number].items():
                pattern, _, _, board_position, board_stop = entry
                stops = table.pattern_stop_slice(pattern)
                positions = list(stops)
                try:
                    exit_position = positions.index(stop)
                except ValueError:
                    continue
                for step in range(board_position, exit_position):
                    segments.add((pattern, int(positions[step]), int(positions[step + 1])))
        return sorted(segments)

    def _provenance(self, day, departure, speed_kmh, minutes, max_transfers) -> dict:
        return {
            "travel_mode": self.mode,
            "network_kind": "walk",
            "classification": "analytical result",
            "algorithm": "walk Dijkstra + RAPTOR (round-based transit search) + walk Dijkstra",
            "routing_method": "walk + wait + ride + transfer + walk",
            "network_source": self.streets.source_name,
            "mode": self.streets.mode,
            "walking_speed_kmh": speed_kmh,
            "speed_kmh": speed_kmh,
            "time_budget_minutes": minutes,
            "max_transfers": max_transfers,
            "departure_time": departure.isoformat(timespec="minutes"),
            "timezone": "Europe/Zurich",
            "service_date": day.isoformat(),
            "distance_crs": self.streets.provenance.get("metric_crs"),
            "network": self.streets.provenance,
            "fallback_reason": self.streets.fallback_reason,
            "services_mode": self.services.mode,
            "services_fallback_reason": self.services.fallback_reason,
            "transit": self.transit.provenance,
            "transit_mode": self.transit.mode,
            "transit_fallback_reason": self.transit.fallback_reason,
        }

    # -- one journey ----------------------------------------------------------
    def route_to_service(self, lat: float, lon: float, service_id: str,
                         minutes: float = 60.0, departure_time=None,
                         max_transfers: int = DEFAULT_MAX_TRANSFERS,
                         walking_speed_kmh: Optional[float] = None) -> dict:
        """The full itinerary to one service: walk, wait, ride, transfer, walk."""
        service = self.services.get(service_id)
        if service is None:
            raise UnknownServiceError(f"No prepared service with id '{service_id}'.")
        if not service.is_routable_on("walk"):
            raise UnroutableServiceError(
                f"'{service.display_name}' is not attached to the walking network.",
                service_id=service_id)
        result = self.calculate(lat, lon, minutes=minutes, departure_time=departure_time,
                                max_transfers=max_transfers, walking_speed_kmh=walking_speed_kmh,
                                categories=[service.category], include_service_items=True,
                                include_geometry=False)
        row = result["reachable_services"].get(service.category.value, {})
        match = next((item for item in row.get("items", []) if item["id"] == service_id), None)
        if match is None:
            raise UnroutableServiceError(
                f"'{service.display_name}' cannot be reached within {minutes:g} minutes "
                f"leaving at {result['departure_time']}.",
                service_id=service_id, minutes=minutes)
        journey = match["journey"]
        return {
            "origin": result["origin"],
            "service": service.summary("walk"),
            "mode": self.mode,
            "mode_label": self.label,
            "departure_time": result["departure_time"],
            "service_date": result["service_date"],
            "walking_speed_kmh": result["walking_speed_kmh"],
            "max_transfers": result["max_transfers"],
            "travel_time_minutes": journey["total_minutes"],
            "journey": journey,
            "geometry": self._journey_geometry(lat, lon, service, journey),
            "provenance": result["provenance"],
        }

    def _journey_geometry(self, lat, lon, service, journey) -> dict:
        """Origin walk leg, each ride segment, and the final walk leg."""
        features = []
        graph = self.streets.graph
        rides = [leg for leg in journey.get("legs", []) if leg["kind"] == "ride"]
        try:
            origin_node, _ = self.streets.nearest_node(lat, lon)
        except Exception:
            return {"type": "FeatureCollection", "features": features}

        def path_feature(start_node, end_node, kind):
            try:
                path = nx.shortest_path(graph, start_node, end_node, weight="length_m")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return None
            lines = [graph[a][b]["geom"] for a, b in zip(path, path[1:])]
            if not lines:
                return None
            from shapely.ops import unary_union
            return {
                "type": "Feature",
                "geometry": _round_geometry(mapping(unary_union(lines))),
                "properties": {"kind": kind},
            }

        service_node = service.access_for("walk").node_id
        if not rides:
            leg = path_feature(origin_node, service_node, "walk_leg")
            if leg:
                features.append(leg)
            return {"type": "FeatureCollection", "features": features}

        board_id = rides[0]["from_stop"]["access"]["node_id"]
        exit_id = rides[-1]["to_stop"]["access"]["node_id"]
        for start_node, end_node, kind in ((origin_node, board_id, "walk_leg"),
                                           (exit_id, service_node, "walk_leg_final")):
            if start_node and end_node:
                leg = path_feature(start_node, end_node, kind)
                if leg:
                    features.append(leg)
        for leg in journey["legs"]:
            if leg["kind"] == "ride":
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [
                        [leg["from_stop"]["lon"], leg["from_stop"]["lat"]],
                        [leg["to_stop"]["lon"], leg["to_stop"]["lat"]],
                    ]},
                    "properties": {"kind": "transit_leg", "route": leg["route"]["label"]},
                })
            else:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [
                        [leg["from_stop"]["lon"], leg["from_stop"]["lat"]],
                        [leg["to_stop"]["lon"], leg["to_stop"]["lat"]],
                    ]},
                    "properties": {"kind": "transfer_leg"},
                })
        return {"type": "FeatureCollection", "features": features}


def _positive(value, field):
    from .errors import InvalidCoordinateError

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InvalidCoordinateError(f"{field} must be a number")
    if not (number > 0) or number != number:
        raise InvalidCoordinateError(f"{field} must be greater than zero")
    return number


def _round_geometry(geometry: dict) -> dict:
    from .accessibility import _round_geometry as rounder

    return rounder(geometry)
