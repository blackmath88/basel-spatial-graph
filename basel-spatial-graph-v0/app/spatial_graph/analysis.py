"""The bridge from graph queries to the routing engines.

Accessibility is not stored in the graph — it depends on a mode, a budget and
(for transit) a departure time, so persisting it would mean millions of edges
that go stale the moment a parameter changes. Instead a query can *call* the
reference application's engines, which is what this module wires up.

Results are memoized per (neighbourhood, mode, minutes, departure): a query that
compares 21 neighbourhoods across three modes asks for the same profile many
times, and each profile is a real Dijkstra or RAPTOR run.
"""
from __future__ import annotations

from typing import Dict, Optional

from ..errors import QuerySpecError
from ..modes import TravelMode, mode_label, parse_mode
from ..service_model import parse_category

# Every accessibility figure this module produces is a live computation, not a
# stored fact. Results say so.
RESULT_KIND = "dynamic analytical computation"


class AccessibilityAnalysis:
    """Runs the walking / cycling / transit engines for graph rows."""

    def __init__(self, engines: Dict[TravelMode, object], origin_method: str = ""):
        self.engines = engines
        self.origin_method = origin_method
        self._cache: Dict[tuple, dict] = {}
        self.calls = 0
        self.cache_hits = 0
        self._trace = []

    @property
    def available_modes(self):
        return [mode.value for mode in self.engines]

    def clear_cache(self) -> None:
        self._cache.clear()

    def begin_trace(self) -> None:
        self._trace = []

    def traced_computations(self) -> list:
        """Semantic computations requested since begin_trace(), deduplicated."""
        result = {}
        for item in self._trace:
            key = item["id"]
            if key in result and result[key] != item:
                suffix = 2
                while f"{key}_{suffix}" in result:
                    suffix += 1
                item = {**item, "id": f"{key}_{suffix}"}
                key = item["id"]
            result.setdefault(key, item)
        return list(result.values())

    def _record_trace(self, profile: dict, travel_mode) -> None:
        provenance = dict(profile.get("provenance") or {})
        provenance["origin_method"] = self.origin_method
        provenance["service_sources"] = self.service_sources(travel_mode)
        self._trace.append({"id": f"{travel_mode.value}_accessibility", **provenance})

    # -- the runner the query engine calls ------------------------------------
    def __call__(self, node: dict, kind: str, params: dict) -> dict:
        if kind != "accessibility":
            raise QuerySpecError(f"Unsupported analysis '{kind}'.", known=["accessibility"])
        return self.accessibility(node, **params)

    def accessibility(self, node: dict, mode: str = "walk", minutes: float = 15,
                      category: Optional[str] = None, departure_time: Optional[str] = None,
                      max_transfers: int = 1) -> dict:
        """What one neighbourhood's representative origin can reach."""
        lat = node.get("representative_lat")
        lon = node.get("representative_lon")
        if lat is None or lon is None:
            raise QuerySpecError(
                f"'{node.get('id')}' has no representative origin, so accessibility "
                "cannot be computed for it.")
        travel_mode = parse_mode(mode)
        wanted = parse_category(category) if category else None
        profile = self._profile(node["id"], lat, lon, travel_mode, float(minutes),
                                departure_time, int(max_transfers))
        row = profile["by_category"].get(wanted.value) if wanted else None
        provenance = dict(profile["provenance"])
        provenance["origin_method"] = self.origin_method
        provenance["service_sources"] = self.service_sources(
            travel_mode, wanted.value if wanted else None)
        return {
            "kind": RESULT_KIND,
            "mode": travel_mode.value,
            "mode_label": mode_label(travel_mode),
            "minutes": float(minutes),
            "category": wanted.value if wanted else None,
            "count": row["count"] if row else profile["total_count"],
            "nearest_minutes": row["nearest_minutes"] if row else None,
            "nearest_name": row["nearest_name"] if row else None,
            "completeness": profile["completeness"],
            "per_category": profile["by_category"] if wanted is None else None,
            "origin": {"lat": lat, "lon": lon, "method": self.origin_method},
            "departure_time": profile.get("departure_time"),
            "service_date": profile.get("service_date"),
            "stops_in_walking_range": profile.get("stops_in_walking_range"),
            "provenance": provenance,
        }

    def service_sources(self, mode, category: Optional[str] = None) -> list:
        """Distinct observed POI datasets used by one analysis."""
        travel_mode = parse_mode(mode) if isinstance(mode, str) else mode
        engine = self.engines.get(travel_mode)
        services = getattr(getattr(engine, "services", None), "services", [])
        grouped = {}
        for service in services:
            if category and service.category.value != category:
                continue
            provenance = service.provenance
            # A source URL may identify one OSM feature. This registry is for
            # contributing provider/dataset pairs, not every reachable POI.
            key = (provenance.get("source"), provenance.get("dataset"))
            group = grouped.setdefault(key, {
                name: provenance.get(name) for name in
                ("source", "dataset", "license", "retrieved_at")})
            group.setdefault("_source_urls", set()).add(provenance.get("source_url"))
        rows = []
        for key in sorted(grouped, key=lambda item: tuple(str(value or "") for value in item)):
            group = grouped[key]
            urls = {url for url in group.pop("_source_urls") if url}
            # Do not present one record URL as if it described the whole
            # provider/dataset dependency.
            group["source_url"] = next(iter(urls)) if len(urls) == 1 else None
            rows.append(group)
        return rows

    def profile(self, node: dict, mode: str = "walk", minutes: float = 15,
                departure_time: Optional[str] = None, max_transfers: int = 1) -> dict:
        """Every category at once — what the five standing questions use."""
        return self._profile(node["id"], node["representative_lat"], node["representative_lon"],
                             parse_mode(mode), float(minutes), departure_time, int(max_transfers))

    # -- internals ------------------------------------------------------------
    def _profile(self, node_id, lat, lon, travel_mode: TravelMode, minutes: float,
                 departure_time, max_transfers) -> dict:
        key = (node_id, travel_mode.value, minutes, departure_time, max_transfers)
        if key in self._cache:
            self.cache_hits += 1
            profile = self._cache[key]
            self._record_trace(profile, travel_mode)
            return profile
        engine = self.engines.get(travel_mode)
        if engine is None:
            raise QuerySpecError(
                f"Travel mode '{travel_mode.value}' is not available in this build.",
                known=self.available_modes)
        self.calls += 1
        if travel_mode is TravelMode.TRANSIT:
            result = engine.calculate(lat, lon, minutes, departure_time=departure_time,
                                      max_transfers=max_transfers,
                                      include_service_items=False, include_geometry=False)
        else:
            result = engine.calculate(lat, lon, minutes, include_service_items=False,
                                      include_geometry=False, include_straight_line=False)
        rows = {
            name: {"count": row["count"], "nearest_minutes": row["nearest_minutes"],
                   "nearest_name": row["nearest_name"], "label": row["label"],
                   "essential": row["essential"]}
            for name, row in result["reachable_services"].items()
        }
        profile = {
            "node_id": node_id,
            "mode": travel_mode.value,
            "minutes": minutes,
            "by_category": rows,
            "total_count": sum(row["count"] for row in rows.values()),
            "completeness": {
                "reachable_count": result["completeness"]["reachable_count"],
                "total": result["completeness"]["total"],
                "missing": result["completeness"]["missing_categories"],
                "label": result["completeness"]["label"],
            },
            "departure_time": result.get("departure_time"),
            "service_date": result.get("service_date"),
            "stops_in_walking_range": (result.get("transit") or {}).get("stops_in_walking_range"),
            # Keep the engine's semantic metadata intact. Its generated_at is
            # deliberately excluded so cached and uncached results describe
            # the same computation.
            "provenance": {key: value for key, value in result["provenance"].items()
                           if key != "generated_at"},
        }
        self._cache[key] = profile
        self._record_trace(profile, travel_mode)
        return profile

    def stats(self) -> dict:
        return {"engine_calls": self.calls, "cache_hits": self.cache_hits,
                "cached_profiles": len(self._cache), "modes": self.available_modes}
