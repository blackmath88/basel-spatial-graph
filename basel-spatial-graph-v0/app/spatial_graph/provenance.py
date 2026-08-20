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
    metadata = graph.metadata or {}
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "graph_generated_at": metadata.get("generated_at"),
        "graph_mode": metadata.get("mode"),
        "datasets": datasets_used(graph, types),
        "relations_traversed": relations,
        "analyses": analyses,
        "origin_method": metadata.get("origin_method") if analyses else None,
        "population_reference_year": metadata.get("population_reference_year"),
        "classification_key": CLASSIFICATIONS,
        "analysis_engine": analysis_stats,
    }
    if spec.group_aggregates:
        result["aggregation"] = {
            "classification": "derived",
            "group_by": spec.group_by,
            "computations": [item.describe() for item in spec.group_aggregates],
            "null_semantics": "Missing values are ignored; numeric aggregates with no values return null.",
        }
    return result
