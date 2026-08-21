"""Where every number in an answer came from.

The graph mixes four kinds of statement and they are not equally trustworthy:

    observed     a shop exists at this point, someone recorded it
    official     21,000 people live in this neighbourhood, the canton counted them
    derived      this shop is inside that neighbourhood — we computed that once
    dynamic      you can reach 7 pharmacies in 15 minutes — we computed that just now,
                 for these parameters, and it would differ for others

Every query result can say which of those it used, from which dataset, for which
reference year. That is what makes the layer usable by something that cannot
inspect the code — a report, a colleague, or later an agent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from .schema import RELATION_TYPES
from ..data_quality import relevant_caveats
from ..snapshot import runtime_snapshot

CLASSIFICATIONS = {
    "observed": "Recorded by a data provider (a POI, a stop, a timetable entry).",
    "official": "Published by an official statistical source.",
    "derived": "Computed once from observed data and persisted (containment, adjacency, snapping).",
    "dynamic": "Computed for this request from the routing engines; depends on its parameters.",
}


def relation_provenance(relation: str) -> dict:
    rel = RELATION_TYPES.get(relation)
    if rel is None:
        return {"relation": relation, "known": False}
    return {
        "relation": relation,
        "description": rel.description,
        "persisted": rel.kind == "structural",
        "classification": "derived" if rel.kind == "structural" else "dynamic",
        "computed_by": rel.computed_by,
    }


def entity_provenance(node: dict) -> dict:
    """What is known about where one node came from."""
    provenance = node.get("provenance") or {}
    node_type = node.get("type")
    classification = "official" if node_type in {"Neighborhood", "PopulationObservation"} else "observed"
    if node_type in {"StreetAccessPoint", "ServiceCategory"}:
        classification = "derived"
    return {
        "id": node.get("id"),
        "type": node_type,
        "classification": classification,
        "data_mode": provenance.get("mode"),
        "explanation": CLASSIFICATIONS[classification],
        "source": provenance.get("source"),
        "dataset": provenance.get("dataset") or provenance.get("feed"),
        "dataset_title": provenance.get("dataset_title"),
        "source_url": provenance.get("source_url"),
        "license": provenance.get("license"),
        "retrieved_at": provenance.get("retrieved_at"),
        "reference_year": node.get("reference_year") or node.get("year"),
        "age_group_definitions": provenance.get("age_group_definitions"),
    }


# Fields denormalized onto a node type from a *different* dataset, so that a
# query touching only `Neighborhood` still credits the source of `children`.
DENORMALIZED_FROM = {
    "Neighborhood": [("population", ["population_total", "children", "young",
                                     "working_age", "elderly", "elderly_80_plus",
                                     "child_share", "elderly_share", "reference_year"])],
}


def datasets_used(graph, types: List[str]) -> List[dict]:
    """One row per distinct dataset behind the node types a query touched.

    Includes datasets whose values are denormalized onto another type: the child
    population lives on `Neighborhood`, but it came from the population dataset
    and that has to be visible in the answer.
    """
    seen, rows = set(), []
    sources = (graph.metadata or {}).get("sources", {})
    for node_type in types:
        for source_key, fields in DENORMALIZED_FROM.get(node_type, []):
            provenance = sources.get(source_key) or {}
            key = (provenance.get("source"), provenance.get("dataset"))
            if key == (None, None) or key in seen:
                continue
            seen.add(key)
            rows.append({
                "for": f"{node_type} (denormalized fields)",
                "fields": fields,
                "source": provenance.get("source"),
                "dataset": provenance.get("dataset"),
                "dataset_title": provenance.get("dataset_title"),
                "source_url": provenance.get("source_url"),
                "license": provenance.get("license"),
                "retrieved_at": provenance.get("retrieved_at"),
                "reference_year": (graph.metadata or {}).get("population_reference_year"),
                "age_group_definitions": provenance.get("age_group_definitions"),
            })
    for node_type in types:
        for node in graph.nodes_of_type(node_type):
            provenance = node.get("provenance") or {}
            key = (provenance.get("source"), provenance.get("dataset") or provenance.get("feed"))
            if key == (None, None) or key in seen:
                continue
            seen.add(key)
            rows.append({
                "for": node_type,
                "source": provenance.get("source"),
                "dataset": provenance.get("dataset") or provenance.get("feed"),
                "dataset_title": provenance.get("dataset_title"),
                "source_url": provenance.get("source_url"),
                "license": provenance.get("license"),
                "retrieved_at": provenance.get("retrieved_at"),
                "reference_year": node.get("reference_year") or node.get("year"),
            })
            break   # one representative per type is enough; they share a provider
    return rows


def _source_record(classification: str, provenance: dict, **extra) -> dict:
    return {
        "classification": classification,
        "data_mode": provenance.get("mode"),
        "source": provenance.get("source"),
        "dataset": provenance.get("dataset") or provenance.get("feed"),
        "dataset_title": provenance.get("dataset_title"),
        "source_url": provenance.get("source_url"),
        "license": provenance.get("license"),
        "retrieved_at": provenance.get("retrieved_at"),
        **extra,
    }


def source_registry(graph, computations=None) -> dict:
    """Compact, stable source records shared by field derivations."""
    metadata = graph.metadata or {}
    raw = metadata.get("sources") or {}
    population = raw.get("population") or {}
    sources = {}
    if population:
        sources["population"] = _source_record(
            "official", population,
            reference_year=metadata.get("population_reference_year"),
            age_group_definitions=population.get("age_group_definitions"),
        )
        if metadata.get("mode") == "fixture":
            sources["population"]["data_mode"] = "fixture"

    for computation in computations or []:
        network = computation.get("network") or {}
        mode = computation.get("network_kind") or computation.get("travel_mode")
        if network:
            key = f"{mode or 'routing'}_network"
            sources[key] = _source_record("observed", network)
        transit = computation.get("transit") or {}
        if transit:
            sources["transit_feed"] = _source_record("observed", transit)
        for index, item in enumerate(computation.get("service_sources") or []):
            key = "service_" + str(index + 1)
            while key in sources and (
                    sources[key].get("dataset"), sources[key].get("source")) != (
                        item.get("dataset"), item.get("source")):
                index += 1
                key = "service_" + str(index + 1)
            sources[key] = _source_record("observed", item)
            if metadata.get("mode") == "fixture" and sources[key].get("data_mode") is None:
                sources[key]["data_mode"] = "fixture"
    for source in sources.values():
        if source.get("data_mode") is None:
            source["data_mode"] = metadata.get("mode")
    return sources


def _computation_registry(computations, sources) -> dict:
    result = {}
    for index, raw in enumerate(computations or []):
        item = dict(raw)
        key = item.pop("id", None) or item.pop("name", None) or f"analysis_{index + 1}"
        refs = []
        network_kind = item.get("network_kind") or item.get("travel_mode")
        if item.get("network") and f"{network_kind}_network" in sources:
            refs.append(f"{network_kind}_network")
        if item.get("transit") and "transit_feed" in sources:
            refs.append("transit_feed")
        for source_key, source in sources.items():
            if not source_key.startswith("service_"):
                continue
            if any(source.get("dataset") == row.get("dataset") and
                   source.get("source") == row.get("source")
                   for row in item.get("service_sources") or []):
                refs.append(source_key)
        item.pop("service_sources", None)
        if item.get("classification") != "derived":
            item["classification"] = "dynamic"
        item["source_refs"] = refs
        result[key] = item
    return result


def _entity_source_refs(graph, sources: dict, type_name: str, filters=()) -> List[str]:
    """Register every provider/dataset contributing matching graph records."""
    classification = "official" if type_name in {"Neighborhood", "PopulationObservation"} \
        else "observed"
    refs = []
    seen = set()
    for node in graph.nodes_of_type(type_name):
        if filters and not all(rule.matches(node) for rule in filters):
            continue
        provenance = node.get("provenance") or {}
        identity = (provenance.get("source"),
                    provenance.get("dataset") or provenance.get("feed"))
        if identity == (None, None) or identity in seen:
            continue
        seen.add(identity)
        existing = next((key for key, row in sources.items()
                         if (row.get("source"), row.get("dataset")) == identity), None)
        if existing:
            refs.append(existing)
            continue
        base = "entity_" + type_name.lower()
        key, suffix = base, 2
        while key in sources:
            key, suffix = f"{base}_{suffix}", suffix + 1
        sources[key] = _source_record(classification, provenance)
        if sources[key].get("data_mode") is None:
            sources[key]["data_mode"] = (graph.metadata or {}).get("mode")
        refs.append(key)
    return refs


def shared_provenance(graph, *, types=None, relations=None, analyses=None,
                      computations=None, fields=None, quality=None,
                      analysis_stats=None) -> dict:
    """One assembler for structured queries and standing questions."""
    metadata = graph.metadata or {}
    computations = computations or []
    sources = source_registry(graph, computations)
    return {
        "version": "1.1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "graph_generated_at": metadata.get("generated_at"),
        "graph_mode": metadata.get("mode"),
        # Frozen snapshot, prepared locally, or fixture — the same distinction
        # /health draws, carried into every answer that leaves the process.
        "data_state": runtime_snapshot().block("spatial_graph", metadata.get("mode")),
        "origin_method": metadata.get("origin_method") if analyses else None,
        "population_reference_year": metadata.get("population_reference_year"),
        "datasets": datasets_used(graph, types or []),
        "relations_traversed": relations or [],
        "analyses": analyses or [],
        "classification_key": CLASSIFICATIONS,
        "analysis_engine": analysis_stats,
        "sources": sources,
        "computations": _computation_registry(computations, sources),
        "fields": fields or {},
        "quality": quality or {"available": False, "caveats": []},
    }


def query_provenance(graph, spec, analysis_stats: Optional[dict] = None) -> dict:
    """The provenance block attached to every relational query answer."""
    types = [spec.start_type] + [
        step.target_type for step in spec.traverse if step.target_type
    ]
    relations = [relation_provenance(step.relation) for step in spec.traverse]
    analyses = [
        {
            "type": step.kind,
            "parameters": step.params,
            "classification": "dynamic",
            "explanation": CLASSIFICATIONS["dynamic"],
            "computed_by": "app.accessibility / app.multimodal",
        }
        for step in spec.analyses
    ]
    actual = (analysis_stats or {}).pop("computations", [])
    fields = {}
    for path in spec.return_fields or []:
        for step in spec.analyses:
            if path.startswith(step.name + "."):
                fields[f"results[].{path}"] = {
                    "classification": "dynamic", "computation_ref": step.name}
        leaf = path.rsplit(".", 1)[-1]
        if spec.start_type == "Neighborhood" and leaf in DENORMALIZED_FROM["Neighborhood"][0][1]:
            fields[f"results[].{path}"] = {
                "classification": "official", "source_refs": ["population"]}
    quality = relevant_caveats((graph.metadata or {}).get("data_quality"),
                               categories=[s.params.get("category") for s in spec.analyses
                                           if s.params.get("category")],
                               networks=[s.params.get("mode", "walk") for s in spec.analyses],
                               transit=any(s.params.get("mode") == "transit" for s in spec.analyses))
    result = shared_provenance(
        graph, types=types, relations=relations, analyses=analyses,
        computations=actual, fields=fields, quality=quality,
        analysis_stats=analysis_stats)

    start_refs = _entity_source_refs(
        graph, result["sources"], spec.start_type, spec.start_filters)
    traversal_refs = {
        step.name: _entity_source_refs(
            graph, result["sources"],
            step.target_type or RELATION_TYPES[step.relation].targets[0], step.filters)
        for step in spec.traverse
    }
    for path in spec.return_fields or []:
        field_key = f"results[].{path}"
        if field_key in result["fields"]:
            continue
        head, dot, leaf = path.partition(".")
        traversal = next((step for step in spec.traverse if step.name == head), None)
        if traversal:
            if leaf == "count":
                result["fields"][field_key] = {
                    "classification": "derived", "relation": traversal.relation,
                    "method": "count distinct traversed target nodes",
                    "source_refs": traversal_refs.get(head, [])}
            else:
                result["fields"][field_key] = {
                    "classification": "observed",
                    "source_refs": traversal_refs.get(head, [])}
        elif not dot or head == spec.start_type:
            result["fields"][field_key] = {
                "classification": ("official" if spec.start_type in
                                   {"Neighborhood", "PopulationObservation"} else "observed"),
                "source_refs": start_refs}

    if spec.group_aggregates:
        result["aggregation"] = {
            "classification": "derived",
            "group_by": spec.group_by,
            "computations": [item.describe() for item in spec.group_aggregates],
            "null_semantics": "Missing values are ignored; numeric aggregates with no values return null.",
        }
        for aggregate in spec.group_aggregates:
            source_refs = start_refs
            if aggregate.field and "." in aggregate.field:
                source_refs = traversal_refs.get(aggregate.field.split(".", 1)[0], start_refs)
            result["computations"][aggregate.alias] = {
                "classification": "derived", "method": aggregate.function,
                "input_field": aggregate.field or "*", "group_by": spec.group_by,
                "null_semantics": result["aggregation"]["null_semantics"],
                "source_refs": source_refs,
            }
            result["fields"][f"results[].{aggregate.alias}"] = {
                "classification": "derived", "computation_ref": aggregate.alias}
        for path in spec.group_by:
            result["fields"].setdefault(
                f"results[].{path}",
                {"classification": ("official" if spec.start_type in
                                    {"Neighborhood", "PopulationObservation"} else "observed"),
                 "source_refs": start_refs})
    return result
