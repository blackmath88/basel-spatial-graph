"""The standing cross-domain questions, answered without a map.

Each one combines persisted graph structure with a live routing computation and
says which is which. They are ordinary functions so the CLI, the API and a test
can all call them, and so the thresholds are arguments rather than opinions
buried in code.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from ..service_model import ESSENTIAL_CATEGORIES, ServiceCategory, category_label

DEFAULT_MINUTES = 15.0
ESSENTIAL = [c.value for c in ESSENTIAL_CATEGORIES]


def _stamp(**extra) -> dict:
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}


def _neighborhoods(graph) -> List[dict]:
    return [n for n in graph.nodes_of_type("Neighborhood")
            if n.get("representative_lat") is not None]


def _row(node: dict, **extra) -> dict:
    return {"id": node["id"], "name": node.get("name"),
            "population_total": node.get("population_total"),
            "children": node.get("children"), "elderly": node.get("elderly"), **extra}


def _median(values: Sequence[float]) -> Optional[float]:
    numbers = [float(v) for v in values if v is not None]
    return statistics.median(numbers) if numbers else None


# --- Q1 -----------------------------------------------------------------------
def poorest_category_access(graph, analysis, category: str = "pharmacy", mode: str = "walk",
                            minutes: float = DEFAULT_MINUTES, limit: int = 10,
                            departure_time: Optional[str] = None) -> dict:
    """Q1 — which neighbourhoods have the poorest access to one category?"""
    parsed = ServiceCategory.parse(category)
    rows = []
    for node in _neighborhoods(graph):
        profile = analysis.profile(node, mode=mode, minutes=minutes,
                                   departure_time=departure_time)
        entry = profile["by_category"].get(parsed.value, {})
        rows.append(_row(node,
                         reachable_count=entry.get("count", 0),
                         nearest_minutes=entry.get("nearest_minutes"),
                         nearest_name=entry.get("nearest_name")))
    # Worst first: fewest reachable, then the longest walk to the nearest one.
    rows.sort(key=lambda r: (r["reachable_count"],
                             -(r["nearest_minutes"] if r["nearest_minutes"] is not None else -1)))
    return _stamp(
        question=f"Which neighbourhoods have the poorest {parsed.value} accessibility by {mode}?",
        category=parsed.value, category_label=category_label(parsed),
        mode=mode, minutes=minutes,
        results=rows[:limit], total_neighborhoods=len(rows),
        methodology=(
            f"For every neighbourhood, the {mode} accessibility engine is run from its "
            f"representative origin with a {minutes:g}-minute budget and the reachable "
            f"{parsed.value} locations are counted. Ranked by count ascending, then by the "
            "walk to the nearest one descending. Counts are a live computation, not a stored "
            "relation; the origin is the neighbourhood's representative point."),
    )


# --- Q2 -----------------------------------------------------------------------
def good_schools_poor_healthcare(graph, analysis, mode: str = "walk",
                                 minutes: float = DEFAULT_MINUTES,
                                 school_min: Optional[int] = None,
                                 healthcare_max: Optional[int] = None,
                                 limit: int = 10, departure_time: Optional[str] = None) -> dict:
    """Q2 — good school access but poor healthcare access, on explicit thresholds."""
    rows = []
    for node in _neighborhoods(graph):
        profile = analysis.profile(node, mode=mode, minutes=minutes,
                                   departure_time=departure_time)
        rows.append(_row(
            node,
            school_count=profile["by_category"].get("school", {}).get("count", 0),
            school_nearest_minutes=profile["by_category"].get("school", {}).get("nearest_minutes"),
            healthcare_count=profile["by_category"].get("healthcare", {}).get("count", 0),
            healthcare_nearest_minutes=profile["by_category"].get("healthcare", {}).get("nearest_minutes"),
        ))
    # Thresholds default to the medians of this very distribution, so they are
    # a property of Basel rather than a number someone liked.
    school_median = _median([r["school_count"] for r in rows])
    healthcare_median = _median([r["healthcare_count"] for r in rows])
    school_min = school_min if school_min is not None else school_median
    healthcare_max = healthcare_max if healthcare_max is not None else healthcare_median
    matched = [r for r in rows
               if r["school_count"] >= school_min and r["healthcare_count"] <= healthcare_max]
    for row in matched:
        row["gap"] = row["school_count"] - row["healthcare_count"]
    matched.sort(key=lambda r: -r["gap"])
    return _stamp(
        question="Which neighbourhoods have good school accessibility but poor healthcare accessibility?",
        mode=mode, minutes=minutes,
        thresholds={"school_count_at_least": school_min, "healthcare_count_at_most": healthcare_max,
                    "source": "medians across all neighbourhoods unless overridden",
                    "school_median": school_median, "healthcare_median": healthcare_median},
        results=matched[:limit], total_neighborhoods=len(rows),
        methodology=(
            "Both counts come from the same live accessibility run per neighbourhood. A "
            "neighbourhood qualifies when its school count is at or above the threshold and its "
            "healthcare count at or below it. Ranked by the size of the gap. The thresholds are "
            "the medians of the observed distribution and are returned so the rule can be "
            "checked or changed; no weighting or scoring is applied."),
    )


# --- Q3 -----------------------------------------------------------------------
def adjacent_contrasts(graph, analysis, mode: str = "walk", minutes: float = DEFAULT_MINUTES,
                       limit: int = 10, departure_time: Optional[str] = None) -> dict:
    """Q3 — which neighbours differ most? Uses ADJACENT_TO plus live accessibility."""
    profiles: Dict[str, dict] = {}
    for node in _neighborhoods(graph):
        profiles[node["id"]] = (node, analysis.profile(node, mode=mode, minutes=minutes,
                                                       departure_time=departure_time))
    seen, pairs = set(), []
    for node_id, (node, profile) in profiles.items():
        for row in graph.neighbors(node_id, relation="ADJACENT_TO"):
            other_id = row["node"]["id"]
            if other_id not in profiles or (other_id, node_id) in seen:
                continue
            seen.add((node_id, other_id))
            other, other_profile = profiles[other_id]
            counts = {c: profile["by_category"].get(c, {}).get("count", 0) for c in ESSENTIAL}
            other_counts = {c: other_profile["by_category"].get(c, {}).get("count", 0)
                            for c in ESSENTIAL}
            total, other_total = sum(counts.values()), sum(other_counts.values())
            by_category = {c: counts[c] - other_counts[c] for c in ESSENTIAL}
            largest = max(by_category.items(), key=lambda kv: abs(kv[1]))
            pairs.append({
                "a_name": node.get("name"), "b_name": other.get("name"),
                "a": {"id": node_id, "name": node.get("name"), "essential_reachable": total},
                "b": {"id": other_id, "name": other.get("name"), "essential_reachable": other_total},
                "a_reachable": total, "b_reachable": other_total,
                "difference": abs(total - other_total),
                "ratio": round(max(total, other_total) / min(total, other_total), 2)
                if min(total, other_total) else None,
                "largest_category_gap": {"category": largest[0], "difference": abs(largest[1]),
                                         "favours": node.get("name") if largest[1] > 0
                                         else other.get("name")},
                "by_category_difference": by_category,
            })
    pairs.sort(key=lambda p: -p["difference"])
    return _stamp(
        question="Which adjacent neighbourhoods differ most in service accessibility?",
        mode=mode, minutes=minutes,
        results=pairs[:limit], total_pairs=len(pairs),
        _provenance={"types": ["Neighborhood"], "relations": ["ADJACENT_TO"]},
        methodology=(
            "Adjacency is a persisted structural relation (polygon boundary contact). For each "
            "adjacent pair, the six essential categories are counted from a live accessibility "
            "run at each neighbourhood's representative origin and the totals are compared. "
            "The difference is an absolute count difference, deliberately not normalised."),
    )


# --- Q4 -----------------------------------------------------------------------
def category_inequality(graph, analysis, mode: str = "walk", minutes: float = DEFAULT_MINUTES,
                        categories: Optional[Sequence[str]] = None,
                        departure_time: Optional[str] = None) -> dict:
    """Q4 — which categories are most unevenly accessible across Basel?"""
    wanted = [ServiceCategory.parse(c).value for c in categories] if categories else ESSENTIAL
    counts: Dict[str, List[int]] = {c: [] for c in wanted}
    names: Dict[str, List[str]] = {c: [] for c in wanted}
    for node in _neighborhoods(graph):
        profile = analysis.profile(node, mode=mode, minutes=minutes,
                                   departure_time=departure_time)
        for category in wanted:
            counts[category].append(profile["by_category"].get(category, {}).get("count", 0))
            names[category].append(node.get("name"))
    rows = []
    for category in wanted:
        values = counts[category]
        mean = statistics.mean(values) if values else 0
        stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
        order = sorted(range(len(values)), key=lambda i: values[i])
        rows.append({
            "category": category, "label": category_label(ServiceCategory.parse(category)),
            "coefficient_of_variation": round(stdev / mean, 3) if mean else None,
            "mean": round(mean, 1), "stdev": round(stdev, 1),
            "min": min(values) if values else None, "max": max(values) if values else None,
            "range": (max(values) - min(values)) if values else None,
            "worst_neighborhood": names[category][order[0]] if values else None,
            "best_neighborhood": names[category][order[-1]] if values else None,
            "zero_access_neighborhoods": sum(1 for v in values if v == 0),
        })
    rows.sort(key=lambda r: -(r["coefficient_of_variation"] or 0))
    return _stamp(
        question="Which service categories are most unevenly accessible across Basel?",
        mode=mode, minutes=minutes, results=rows,
        measure="coefficient of variation (population standard deviation / mean)",
        results_neighborhood_count=len(counts[wanted[0]]) if wanted else 0,
        methodology=(
            "For each category, the reachable count is computed for every neighbourhood and the "
            "coefficient of variation is taken across those counts. CV is used rather than the "
            "raw range or standard deviation because categories differ hugely in absolute size — "
            "Basel has 415 schools and 63 pharmacies — and dividing by the mean makes the spread "
            "comparable between them. Range and standard deviation are returned too, so the "
            "choice can be second-guessed."),
    )


# --- Q5 -----------------------------------------------------------------------
def mode_gain(graph, analysis, base_mode: str = "walk",
              compare_modes: Sequence[str] = ("bike", "transit"),
              minutes: float = DEFAULT_MINUTES, limit: int = 10,
              departure_time: Optional[str] = None) -> dict:
    """Q5 — which neighbourhoods gain most from cycling or transit?"""
    available = set(analysis.available_modes)
    compare = [m for m in compare_modes if m in available]
    rows = []
    for node in _neighborhoods(graph):
        base = analysis.profile(node, mode=base_mode, minutes=minutes,
                                departure_time=departure_time)
        base_total = sum(base["by_category"].get(c, {}).get("count", 0) for c in ESSENTIAL)
        entry = _row(node, base_mode=base_mode, base_essential_reachable=base_total, gains={})
        for mode in compare:
            other = analysis.profile(node, mode=mode, minutes=minutes,
                                     departure_time=departure_time)
            total = sum(other["by_category"].get(c, {}).get("count", 0) for c in ESSENTIAL)
            entry["gains"][mode] = {
                "essential_reachable": total,
                "absolute_gain": total - base_total,
                "multiplier": round(total / base_total, 2) if base_total else None,
                "completeness_gain": (other["completeness"]["reachable_count"]
                                      - base["completeness"]["reachable_count"]),
                "by_category_gain": {
                    c: (other["by_category"].get(c, {}).get("count", 0)
                        - base["by_category"].get(c, {}).get("count", 0))
                    for c in ESSENTIAL
                },
            }
        entry["best_gain_mode"] = max(
            entry["gains"], key=lambda m: entry["gains"][m]["absolute_gain"]) if entry["gains"] else None
        rows.append(entry)
    ranked = sorted(rows, key=lambda r: -max(
        (g["absolute_gain"] for g in r["gains"].values()), default=0))
    return _stamp(
        question=f"Which neighbourhoods gain the most accessibility when switching from "
                 f"{base_mode} to {' or '.join(compare) if compare else '—'}?",
        base_mode=base_mode, compared_modes=compare, minutes=minutes,
        results=ranked[:limit], total_neighborhoods=len(rows),
        methodology=(
            "The same origin and time budget are used for every mode; only the engine changes. "
            "Counts are the six essential categories summed. Multipliers are reported alongside "
            "absolute gains because a neighbourhood starting from a small base can multiply "
            "impressively while gaining little. All figures are live computations."),
    )


# --- the demographic cross-domain question ------------------------------------
def children_underserved(graph, analysis, min_children: Optional[int] = None,
                         mode: str = "walk", minutes: float = DEFAULT_MINUTES,
                         limit: int = 10, departure_time: Optional[str] = None) -> dict:
    """Statistics + graph + routing: where do many children have poor access?

    "Poor" means below the median of this city, for both pharmacies and transit
    stops within walking range. `min_children` defaults to the median child
    population, so the threshold comes from the data rather than from a
    round number someone picked.
    """
    rows = []
    walk_provenance = None
    transit_provenance = None
    transit_path = "dynamic" if "transit" in analysis.available_modes else "structural"
    fallback_method = "point-in-polygon"
    for node in _neighborhoods(graph):
        walk = analysis.profile(node, mode=mode, minutes=minutes,
                                departure_time=departure_time)
        transit = analysis.profile(node, mode="transit", minutes=minutes,
                                   departure_time=departure_time) \
            if "transit" in analysis.available_modes else {}
        if walk_provenance is None:
            walk_provenance = dict(walk.get("provenance") or {})
            walk_provenance["origin_method"] = analysis.origin_method
            walk_provenance["service_sources"] = analysis.service_sources(mode, "pharmacy")
        if transit and transit_provenance is None:
            transit_provenance = dict(transit.get("provenance") or {})
            transit_provenance["origin_method"] = analysis.origin_method
        stops = transit.get("stops_in_walking_range")
        if stops is None:
            neighbors = graph.neighbors(node["id"], relation="HAS_TRANSIT_STOP")
            stops = len(neighbors)
            if neighbors:
                fallback_method = neighbors[0].get("properties", {}).get("method") or fallback_method
        rows.append(_row(
            node,
            child_share=node.get("child_share"),
            reference_year=node.get("reference_year"),
            pharmacy_count=walk["by_category"].get("pharmacy", {}).get("count", 0),
            pharmacy_nearest_minutes=walk["by_category"].get("pharmacy", {}).get("nearest_minutes"),
            transit_stops_in_walking_range=stops,
        ))
    child_median = _median([r["children"] for r in rows])
    pharmacy_median = _median([r["pharmacy_count"] for r in rows])
    stop_median = _median([r["transit_stops_in_walking_range"] for r in rows])
    threshold = min_children if min_children is not None else child_median
    matched = [
        r for r in rows
        if (r["children"] or 0) > threshold
        and r["pharmacy_count"] < pharmacy_median
        and r["transit_stops_in_walking_range"] < stop_median
    ]
    matched.sort(key=lambda r: -(r["children"] or 0))
    threshold_fields = {
        "children_median": ("children", "gt", False),
        "pharmacy_count_below": ("pharmacy_count", "lt", False),
        "transit_stops_below": ("transit_stops_in_walking_range", "lt", False),
    }
    computations = [{"id": "pharmacy_access", **(walk_provenance or {})}]
    if transit_path == "dynamic":
        computations.append({"id": "transit_stop_access", **(transit_provenance or {})})
    fields = {
        "results[].children": {"classification": "official", "source_refs": ["population"]},
        "results[].pharmacy_count": {
            "classification": "dynamic", "computation_ref": "pharmacy_access"},
        "results[].pharmacy_nearest_minutes": {
            "classification": "dynamic", "computation_ref": "pharmacy_access", "unit": "minutes"},
    }
    if transit_path == "dynamic":
        fields["results[].transit_stops_in_walking_range"] = {
            "classification": "dynamic", "computation_ref": "transit_stop_access",
            "semantics": "stops reachable on foot within the routed time budget"}
        transit_methodology = (
            "Transit access is the number of stops reachable on foot inside the same budget, "
            "from the multimodal engine's first phase.")
    else:
        fields["results[].transit_stops_in_walking_range"] = {
            "classification": "derived", "relation": "HAS_TRANSIT_STOP",
            "method": fallback_method,
            "semantics": ("fallback count of stops physically contained in the neighborhood; "
                          "not stops reachable within the walking-time budget")}
        transit_methodology = (
            "Transit routing was unavailable, so the compatibility field uses persisted "
            "HAS_TRANSIT_STOP containment counts. These are stops physically inside each "
            "neighbourhood, not stops reachable within the walking-time budget.")
    threshold_values = {
        "children_median": child_median,
        "pharmacy_count_below": pharmacy_median,
        "transit_stops_below": stop_median,
    }
    for name, (input_field, operator, overridden) in threshold_fields.items():
        computation_id = f"threshold_{name}"
        computations.append({
            "id": computation_id, "classification": "derived", "method": "median",
            "input_field": input_field, "input_set": "all_neighborhoods",
            "input_count": sum(row.get(input_field) is not None for row in rows),
            "null_semantics": "null values ignored",
            "comparison_operator": operator, "caller_override": overridden,
            "value": threshold_values[name],
        })
        fields[f"thresholds.{name}"] = {
            "classification": "derived", "computation_ref": computation_id}
    computations.append({
        "id": "threshold_children_more_than", "classification": "derived",
        "method": "caller value" if min_children is not None else "median",
        "input_field": "children", "input_set": "all_neighborhoods",
        "input_count": sum(row.get("children") is not None for row in rows),
        "null_semantics": "null values ignored", "comparison_operator": "gt",
        "caller_override": min_children is not None, "value": threshold,
        "median_value": child_median,
    })
    fields["thresholds.children_more_than"] = {
        "classification": "derived", "computation_ref": "threshold_children_more_than"}
    return _stamp(
        question=("Which neighbourhoods with many children have below-median access to both "
                  "pharmacies and public transport?"),
        mode=mode, minutes=minutes,
        thresholds={
            "children_more_than": threshold,
            "children_median": child_median,
            "pharmacy_count_below": pharmacy_median,
            "transit_stops_below": stop_median,
            "source": "medians of the observed Basel distribution unless overridden",
        },
        results=matched[:limit], total_neighborhoods=len(rows),
        all_neighborhoods=rows,
        _provenance={
            "types": ["Neighborhood"], "computations": computations, "fields": fields,
            "quality": {"categories": ["pharmacy"], "networks": ["walk"],
                        "transit": transit_path == "dynamic"},
            "relations": (["HAS_TRANSIT_STOP"] if transit_path == "structural" else []),
        },
        methodology=(
            "Child population is official Basel-Stadt statistics (dataset 100128, ages 0-17) "
            "carried on the Neighborhood node. Pharmacy access is a live walking accessibility "
            f"run from the neighbourhood's representative origin using {mode} mode. "
            f"{transit_methodology} All three thresholds are medians of the observed distribution and are "
            "returned so the rule can be checked or changed."),
    )


QUESTIONS = {
    "q1_poorest_access": poorest_category_access,
    "q2_schools_vs_healthcare": good_schools_poor_healthcare,
    "q3_adjacent_contrasts": adjacent_contrasts,
    "q4_category_inequality": category_inequality,
    "q5_mode_gain": mode_gain,
    "q6_children_underserved": children_underserved,
}
