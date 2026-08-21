"""A small, bounded query language over the heterogeneous graph.

Deliberately not Cypher, not SQL, not Python. A query is a JSON document with
at most six parts, every one of which is validated against the schema before
anything runs:

    start      which node type to begin from, and how to filter it
    traverse   which typed relations to follow, and how to filter what is found
    analyses   deterministic spatial computations to run per row (accessibility)
    aggregate  named numbers derived from the traversed sets
    rank       how to order the rows
    return     which fields to project

The point of keeping it small is that everything it can express is safe, cheap
and explainable. Anything it cannot express is a signal about what the language
should grow next — not a reason to hand a database a string.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..errors import QuerySpecError
from .model import MAX_RESULTS, NetworkXSpatialGraph, public_node
from .schema import FILTER_OPS, NODE_TYPES, RELATION_TYPES, node_type, relation_type

DEFAULT_LIMIT = 50
MAX_TRAVERSE_STEPS = 4
MAX_ANALYSES = 4
AGGREGATE_FUNCTIONS = {"count", "count_distinct", "sum", "avg", "min", "max"}
NUMERIC_FIELD_TYPES = {"integer", "number"}


# --- filters ------------------------------------------------------------------
def _compare(value, op: str, wanted) -> bool:
    if op == "exists":
        return value is not None
    if value is None:
        return False
    try:
        if op == "eq":
            return value == wanted
        if op == "ne":
            return value != wanted
        if op == "gt":
            return float(value) > float(wanted)
        if op == "gte":
            return float(value) >= float(wanted)
        if op == "lt":
            return float(value) < float(wanted)
        if op == "lte":
            return float(value) <= float(wanted)
        if op == "in":
            return value in (wanted or [])
        if op == "not_in":
            return value not in (wanted or [])
        if op == "contains":
            return str(wanted).lower() in str(value).lower()
        if op == "between":
            low, high = wanted
            return float(low) <= float(value) <= float(high)
    except (TypeError, ValueError):
        return False
    return False


@dataclass(frozen=True)
class Filter:
    field: str
    op: str
    value: Any = None

    @classmethod
    def parse(cls, raw: dict, type_name: str) -> "Filter":
        if not isinstance(raw, dict):
            raise QuerySpecError("Each filter must be an object with field/op/value.")
        name = raw.get("field")
        op = raw.get("op", "eq")
        if op not in FILTER_OPS:
            raise QuerySpecError(f"Unknown filter operator '{op}'.",
                                 known=sorted(FILTER_OPS))
        known = set(node_type(type_name).field_names)
        if name not in known:
            raise QuerySpecError(
                f"'{type_name}' has no field '{name}'.", known=sorted(known))
        if op in {"in", "not_in"} and not isinstance(raw.get("value"), (list, tuple)):
            raise QuerySpecError(f"Operator '{op}' needs a list value.")
        if op == "between" and not (isinstance(raw.get("value"), (list, tuple))
                                    and len(raw["value"]) == 2):
            raise QuerySpecError("Operator 'between' needs a [low, high] value.")
        return cls(name, op, raw.get("value"))

    def matches(self, node: dict) -> bool:
        return _compare(node.get(self.field), self.op, self.value)

    def describe(self) -> dict:
        return {"field": self.field, "op": self.op, "value": self.value}


@dataclass(frozen=True)
class TraverseStep:
    relation: str
    target_type: Optional[str]
    filters: Sequence[Filter]
    name: str
    source: Optional[str]          # a previous step's name, or None for the start
    min_count: Optional[int]
    max_count: Optional[int]

    def describe(self) -> dict:
        return {"relation": self.relation, "target_type": self.target_type, "as": self.name,
                "from": self.source, "filters": [f.describe() for f in self.filters],
                "min_count": self.min_count, "max_count": self.max_count}


@dataclass(frozen=True)
class AnalysisStep:
    name: str
    kind: str
    params: dict
    constraint: Optional[dict]

    def describe(self) -> dict:
        return {"as": self.name, "type": self.kind, **self.params,
                "constraint": self.constraint}


@dataclass(frozen=True)
class GroupAggregate:
    function: str
    field: Optional[str]
    alias: str

    def describe(self) -> dict:
        return {"function": self.function, "field": self.field, "alias": self.alias}


@dataclass(frozen=True)
class ResultFilter:
    field: str
    op: str
    value: Any = None

    def matches(self, row: dict) -> bool:
        return _compare(row.get(self.field), self.op, self.value)

    def describe(self) -> dict:
        return {"field": self.field, "op": self.op, "value": self.value}


@dataclass
class QuerySpec:
    start_type: str
    start_filters: List[Filter] = field(default_factory=list)
    start_ids: Optional[List[str]] = None
    traverse: List[TraverseStep] = field(default_factory=list)
    analyses: List[AnalysisStep] = field(default_factory=list)
    aggregate: Dict[str, dict] = field(default_factory=dict)
    group_by: List[str] = field(default_factory=list)
    group_aggregates: List[GroupAggregate] = field(default_factory=list)
    having: List[ResultFilter] = field(default_factory=list)
    order_by: List[dict] = field(default_factory=list)
    rank: Optional[dict] = None
    return_fields: Optional[List[str]] = None
    limit: int = DEFAULT_LIMIT
    include_geometry: bool = False

    # -- parsing ---------------------------------------------------------------
    @classmethod
    def parse(cls, raw: dict) -> "QuerySpec":
        if not isinstance(raw, dict):
            raise QuerySpecError("A query must be a JSON object.")
        start = raw.get("start")
        if not isinstance(start, dict) or not start.get("type"):
            raise QuerySpecError("A query needs a 'start' with a 'type'.",
                                 known=sorted(NODE_TYPES))
        start_type = start["type"]
        node_type(start_type)      # validates, raises with the known list

        spec = cls(
            start_type=start_type,
            start_filters=[Filter.parse(f, start_type) for f in start.get("filters", [])],
            start_ids=list(start["ids"]) if isinstance(start.get("ids"), list) else None,
            limit=cls._limit(raw.get("limit", DEFAULT_LIMIT)),
            include_geometry=bool(raw.get("include_geometry", False)),
        )

        names = {start_type}
        steps = raw.get("traverse", []) or []
        if len(steps) > MAX_TRAVERSE_STEPS:
            raise QuerySpecError(f"At most {MAX_TRAVERSE_STEPS} traverse steps are allowed.")
        for step in steps:
            spec.traverse.append(cls._traverse_step(step, names))

        analyses = (raw.get("analyses") or []) + [
            dict(entry, _implicit=True) for entry in (raw.get("where") or [])
        ]
        if len(analyses) > MAX_ANALYSES:
            raise QuerySpecError(f"At most {MAX_ANALYSES} analyses are allowed.")
        for entry in analyses:
            spec.analyses.append(cls._analysis_step(entry, names, start_type))

        aggregate_raw = raw.get("aggregate") or {}
        if isinstance(aggregate_raw, list):
            spec.group_by = cls._group_by(raw.get("group_by"), start_type, spec.traverse,
                                          spec.analyses)
            spec.group_aggregates = cls._group_aggregates(
                aggregate_raw, start_type, spec.traverse, spec.analyses)
            aliases = {item.alias for item in spec.group_aggregates}
            result_fields = set(spec.group_by) | aliases
            spec.having = cls._result_filters(raw.get("having") or [], result_fields)
            spec.order_by = cls._order_by(raw.get("order_by") or [], result_fields)
        elif raw.get("group_by") or raw.get("having") or raw.get("order_by"):
            raise QuerySpecError(
                "'group_by', 'having' and 'order_by' require list-form 'aggregate'.")
        elif not isinstance(aggregate_raw, dict):
            raise QuerySpecError("'aggregate' must be an object (legacy) or a list (grouped).")

        for name, definition in aggregate_raw.items() if isinstance(aggregate_raw, dict) else []:
            if not isinstance(definition, dict) or "op" not in definition:
                raise QuerySpecError(f"Aggregate '{name}' needs an 'op'.")
            if definition["op"] not in {"count", "sum", "avg", "min", "max"}:
                raise QuerySpecError(f"Unknown aggregate op '{definition['op']}'.",
                                     known=["count", "sum", "avg", "min", "max"])
            spec.aggregate[name] = definition
            names.add(name)

        if raw.get("rank"):
            rank = raw["rank"]
            if not isinstance(rank, dict) or "by" not in rank:
                raise QuerySpecError("'rank' needs a 'by' path.")
            if rank.get("order", "desc") not in {"asc", "desc"}:
                raise QuerySpecError("'rank.order' must be 'asc' or 'desc'.")
            spec.rank = rank

        if raw.get("return"):
            if not isinstance(raw["return"], list):
                raise QuerySpecError("'return' must be a list of field paths.")
            spec.return_fields = list(raw["return"])
            if spec.group_aggregates:
                valid = set(spec.group_by) | {item.alias for item in spec.group_aggregates}
                unknown = [path for path in spec.return_fields if path not in valid]
                if unknown:
                    raise QuerySpecError(
                        f"Grouped return refers to unknown result field '{unknown[0]}'.",
                        known=sorted(valid))
        return spec

    @staticmethod
    def _known_paths(start_type: str, traversals, analyses) -> Dict[str, str]:
        known = {name: field.type for name, field in
                 ((f.name, f) for f in node_type(start_type).fields)}
        known.update({f"{start_type}.{name}": field_type
                      for name, field_type in list(known.items())})
        for step in traversals:
            target = step.target_type or relation_type(step.relation).targets[0]
            for declared in node_type(target).fields:
                known[f"{step.name}.{declared.name}"] = declared.type
            known[f"{step.name}.count"] = "integer"
        for step in analyses:
            for produced in ("count", "nearest_minutes", "completeness"):
                known[f"{step.name}.{produced}"] = "number"
        return known

    @classmethod
    def _group_by(cls, raw, start_type, traversals, analyses) -> List[str]:
        if not isinstance(raw, list) or not raw:
            raise QuerySpecError("A grouped query needs a non-empty 'group_by' list.")
        if not all(isinstance(path, str) and path for path in raw):
            raise QuerySpecError("Every 'group_by' entry must be a field path.")
        known = cls._known_paths(start_type, traversals, analyses)
        unknown = [path for path in raw if path not in known]
        if unknown:
            raise QuerySpecError(f"Unknown grouping field '{unknown[0]}'.", known=sorted(known))
        return list(raw)

    @classmethod
    def _group_aggregates(cls, raw, start_type, traversals, analyses):
        if not raw:
            raise QuerySpecError("A grouped query needs at least one aggregate.")
        known = cls._known_paths(start_type, traversals, analyses)
        parsed, aliases = [], set()
        for entry in raw:
            if not isinstance(entry, dict):
                raise QuerySpecError("Each aggregate must be an object.")
            function = entry.get("function") or entry.get("op")
            if function not in AGGREGATE_FUNCTIONS:
                raise QuerySpecError(f"Unknown aggregate function '{function}'.",
                                     known=sorted(AGGREGATE_FUNCTIONS))
            source = entry.get("field")
            alias = entry.get("as")
            if not isinstance(alias, str) or not alias:
                raise QuerySpecError("Each grouped aggregate needs a non-empty 'as' alias.")
            if alias in aliases:
                raise QuerySpecError(f"Duplicate aggregate alias '{alias}'.")
            if function != "count" and not source:
                raise QuerySpecError(f"Aggregate '{function}' needs a 'field'.")
            if source not in (None, "*") and source not in known:
                raise QuerySpecError(f"Unknown aggregate field '{source}'.", known=sorted(known))
            if function in {"sum", "avg", "min", "max"} and known.get(source) not in NUMERIC_FIELD_TYPES:
                raise QuerySpecError(
                    f"Aggregate '{function}' requires a numeric field; '{source}' is "
                    f"{known.get(source, 'unknown')}.", field=source,
                    valid_fields=sorted(k for k, v in known.items() if v in NUMERIC_FIELD_TYPES))
            aliases.add(alias)
            parsed.append(GroupAggregate(function, source, alias))
        return parsed

    @staticmethod
    def _result_filters(raw, valid_fields) -> List[ResultFilter]:
        if not isinstance(raw, list):
            raise QuerySpecError("'having' must be a list.")
        result = []
        for entry in raw:
            if not isinstance(entry, dict) or entry.get("field") not in valid_fields:
                raise QuerySpecError("HAVING refers to an unknown result field.",
                                     known=sorted(valid_fields))
            op = entry.get("op", "eq")
            if op not in FILTER_OPS:
                raise QuerySpecError(f"Unknown filter operator '{op}'.", known=sorted(FILTER_OPS))
            if op in {"in", "not_in"} and not isinstance(entry.get("value"), (list, tuple)):
                raise QuerySpecError(f"Operator '{op}' needs a list value.")
            if op == "between" and not (isinstance(entry.get("value"), (list, tuple))
                                         and len(entry["value"]) == 2):
                raise QuerySpecError("Operator 'between' needs a [low, high] value.")
            result.append(ResultFilter(entry["field"], op, entry.get("value")))
        return result

    @staticmethod
    def _order_by(raw, valid_fields) -> List[dict]:
        if not isinstance(raw, list):
            raise QuerySpecError("'order_by' must be a list.")
        result = []
        for entry in raw:
            if not isinstance(entry, dict) or entry.get("field") not in valid_fields:
                raise QuerySpecError("ORDER BY refers to an unknown result field.",
                                     known=sorted(valid_fields))
            direction = entry.get("direction", "asc")
            if direction not in {"asc", "desc"}:
                raise QuerySpecError("ORDER BY direction must be 'asc' or 'desc'.")
            result.append({"field": entry["field"], "direction": direction})
        return result

    @staticmethod
    def _limit(value) -> int:
        try:
            limit = int(value)
        except (TypeError, ValueError):
            raise QuerySpecError("'limit' must be a whole number.")
        if limit < 1:
            raise QuerySpecError("'limit' must be at least 1.")
        return min(limit, MAX_RESULTS)

    @staticmethod
    def _traverse_step(step: dict, names: set) -> TraverseStep:
        if not isinstance(step, dict) or not step.get("relation"):
            raise QuerySpecError("Each traverse step needs a 'relation'.",
                                 known=sorted(RELATION_TYPES))
        relation = relation_type(step["relation"])
        if relation.kind != "structural":
            raise QuerySpecError(
                f"'{relation.name}' is an analytical relation and cannot be traversed. "
                "Use an 'analyses' entry instead.",
                computed_by=relation.computed_by)
        target = step.get("target_type")
        if target:
            node_type(target)
            if target not in relation.targets:
                raise QuerySpecError(
                    f"'{relation.name}' does not point at '{target}'.",
                    known=list(relation.targets))
        source = step.get("from")
        if source and source not in names:
            raise QuerySpecError(f"traverse step refers to unknown source '{source}'.",
                                 known=sorted(names))
        name = step.get("as") or (target or relation.targets[0])
        names.add(name)
        filter_type = target or relation.targets[0]
        return TraverseStep(
            relation=relation.name, target_type=target, name=name, source=source,
            filters=[Filter.parse(f, filter_type) for f in step.get("filters", [])],
            min_count=step.get("min_count"), max_count=step.get("max_count"),
        )

    @staticmethod
    def _analysis_step(entry: dict, names: set, start_type: str) -> AnalysisStep:
        from .schema import ANALYSIS_TYPES

        if not isinstance(entry, dict):
            raise QuerySpecError("Each analysis must be an object.")
        kind = entry.get("analysis") or entry.get("type")
        if kind not in ANALYSIS_TYPES:
            raise QuerySpecError(f"Unknown analysis '{kind}'.", known=sorted(ANALYSIS_TYPES))
        if start_type not in ANALYSIS_TYPES[kind]["applies_to"]:
            raise QuerySpecError(
                f"Analysis '{kind}' applies to {ANALYSIS_TYPES[kind]['applies_to']}, "
                f"not '{start_type}'.")
        params = {k: v for k, v in entry.items()
                  if k in {"mode", "minutes", "category", "target_category", "departure_time",
                           "max_transfers"}}
        if "target_category" in params:
            params["category"] = params.pop("target_category")
        constraint = entry.get("constraint")
        if constraint is None and entry.get("operator"):
            # `{"operator": "count_lt", "value": 1}` is the shorthand form.
            operator = entry["operator"]
            if "_" not in operator:
                raise QuerySpecError("Shorthand operators look like 'count_lt'.")
            target, op = operator.rsplit("_", 1)
            constraint = {"field": target, "op": op, "value": entry.get("value")}
        if constraint is not None:
            if constraint.get("op") not in FILTER_OPS:
                raise QuerySpecError(f"Unknown constraint operator '{constraint.get('op')}'.",
                                     known=sorted(FILTER_OPS))
        name = entry.get("as") or f"{kind}_{len(names)}"
        names.add(name)
        return AnalysisStep(name=name, kind=kind, params=params, constraint=constraint)

    def describe(self) -> dict:
        return {
            "start": {"type": self.start_type,
                      "filters": [f.describe() for f in self.start_filters],
                      "ids": self.start_ids},
            "traverse": [step.describe() for step in self.traverse],
            "analyses": [step.describe() for step in self.analyses],
            "aggregate": self.aggregate,
            "group_by": self.group_by,
            "grouped_aggregates": [item.describe() for item in self.group_aggregates],
            "having": [item.describe() for item in self.having],
            "order_by": self.order_by,
            "rank": self.rank,
            "return": self.return_fields,
            "limit": self.limit,
            "include_geometry": self.include_geometry,
        }


# --- execution ----------------------------------------------------------------
def _resolve(path: str, context: dict):
    """`Neighborhood.name`, `pharmacies.count`, `walk15.nearest_minutes`."""
    if path in context:
        return context[path]
    start_type = context.get("_start_type")
    if "." not in path and start_type and isinstance(context.get(start_type), dict):
        return context[start_type].get(path)
    head, _, tail = path.partition(".")
    value = context.get(head)
    if value is None or not tail:
        return value
    if isinstance(value, list):
        if tail == "count":
            return len(value)
        return [item.get(tail) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for part in tail.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value
    return None


class QueryEngine:
    """Runs a `QuerySpec` against the graph, optionally calling the routing engines."""

    def __init__(self, graph: NetworkXSpatialGraph,
                 analysis_runner: Optional[Callable[[dict, str, dict], dict]] = None):
        self.graph = graph
        self.analysis_runner = analysis_runner

    def run(self, spec: QuerySpec) -> dict:
        started = datetime.now(timezone.utc)
        candidates = self._start_nodes(spec)
        scanned = len(candidates)

        rows: List[dict] = []
        analyses_run = 0
        computation_provenance: Dict[str, dict] = {}
        for node in candidates:
            context: Dict[str, Any] = {spec.start_type: node, "id": node.get("id"),
                                       "_start_type": spec.start_type}
            if not self._apply_traversals(spec, context):
                continue
            ok, ran = self._apply_analyses(spec, node, context, computation_provenance)
            analyses_run += ran
            if not ok:
                continue
            self._apply_aggregates(spec, context)
            rows.append(context)

        groups_total = None
        if spec.group_aggregates:
            rows = self._group(spec, rows)
            groups_total = len(rows)
            rows = [row for row in rows if all(rule.matches(row) for rule in spec.having)]
            rows = self._order_grouped(spec, rows)
        else:
            rows = self._rank(spec, rows)
        truncated = len(rows) > spec.limit
        rows = rows[:spec.limit]

        results = [self._project(spec, row) for row in rows]
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        return {
            "results": results,
            "count": len(results),
            "truncated": truncated,
            "execution": {
                "start_type": spec.start_type,
                "candidates_scanned": scanned,
                "filters_applied": len(spec.start_filters),
                "relations_traversed": [step.relation for step in spec.traverse],
                "analyses": [step.describe() for step in spec.analyses],
                "analysis_calls": analyses_run,
                "aggregates": sorted(spec.aggregate),
                "group_by": spec.group_by,
                "grouped_aggregates": [item.describe() for item in spec.group_aggregates],
                "rows_scanned": len(rows) if groups_total is None else scanned,
                "groups_formed": groups_total,
                "groups_returned": len(results) if groups_total is not None else None,
                "having": [item.describe() for item in spec.having],
                "order_by": spec.order_by,
                "ranked_by": spec.rank,
                "limit": spec.limit,
                "include_geometry": spec.include_geometry,
                "elapsed_seconds": round(elapsed, 3),
                "generated_at": started.isoformat(timespec="seconds"),
                "computation_provenance": list(computation_provenance.values()),
            },
            "query": spec.describe(),
        }

    # -- pieces ---------------------------------------------------------------
    def _start_nodes(self, spec: QuerySpec) -> List[dict]:
        if spec.start_ids:
            nodes = []
            for entity_id in spec.start_ids:
                node = self.graph.graph.nodes.get(entity_id)
                if node and node.get("type") == spec.start_type:
                    nodes.append(dict(node))
        else:
            nodes = list(self.graph.nodes_of_type(spec.start_type))
        return [node for node in nodes
                if all(f.matches(node) for f in spec.start_filters)]

    def _apply_traversals(self, spec: QuerySpec, context: dict) -> bool:
        for step in spec.traverse:
            origins = context.get(step.source) if step.source else [context[spec.start_type]]
            if isinstance(origins, dict):
                origins = [origins]
            found: List[dict] = []
            seen = set()
            for origin in origins or []:
                for row in self.graph.neighbors(origin["id"], relation=step.relation,
                                                target_type=step.target_type):
                    node = row["node"]
                    if node["id"] in seen:
                        continue
                    if not all(f.matches(node) for f in step.filters):
                        continue
                    seen.add(node["id"])
                    found.append(node)
            context[step.name] = found
            if step.min_count is not None and len(found) < step.min_count:
                return False
            if step.max_count is not None and len(found) > step.max_count:
                return False
        return True

    def _apply_analyses(self, spec: QuerySpec, node: dict, context: dict,
                        computation_provenance: dict):
        ran = 0
        for step in spec.analyses:
            if self.analysis_runner is None:
                raise QuerySpecError(
                    "This query needs a spatial analysis, but no analysis engine is attached.")
            result = self.analysis_runner(node, step.kind, step.params)
            ran += 1
            context[step.name] = result
            if result.get("provenance"):
                computation_provenance.setdefault(step.name, {
                    "name": step.name, "type": step.kind,
                    "parameters": step.params, **result["provenance"],
                })
            if step.constraint:
                value = _resolve(f"{step.name}.{step.constraint['field']}", context)
                if not _compare(value, step.constraint["op"], step.constraint.get("value")):
                    return False, ran
        return True, ran

    @staticmethod
    def _apply_aggregates(spec: QuerySpec, context: dict) -> None:
        for name, definition in spec.aggregate.items():
            op = definition["op"]
            source = definition.get("of") or definition.get("from")
            if op == "count":
                value = _resolve(f"{source}.count", context) if source else None
                context[name] = value if isinstance(value, int) else len(
                    context.get(source) or [])
                continue
            values = _resolve(source, context) if source else None
            numbers = [float(v) for v in (values or []) if isinstance(v, (int, float))]
            if not numbers:
                context[name] = None
                continue
            context[name] = {
                "sum": sum(numbers),
                "avg": sum(numbers) / len(numbers),
                "min": min(numbers),
                "max": max(numbers),
            }[op]

    @staticmethod
    def _group(spec: QuerySpec, rows: List[dict]) -> List[dict]:
        grouped: Dict[tuple, List[dict]] = {}
        for row in rows:
            values = []
            for path in spec.group_by:
                value = _resolve(path, row)
                values.append(value if isinstance(value, list) else [value])
            for key in itertools.product(*values):
                grouped.setdefault(tuple(key), []).append(row)

        output = []
        for key, members in grouped.items():
            result = dict(zip(spec.group_by, key))
            for aggregate in spec.group_aggregates:
                values = []
                for member in members:
                    resolved = _resolve(aggregate.field, member) if aggregate.field not in (None, "*") else 1
                    values.extend(resolved if isinstance(resolved, list) else [resolved])
                present = [value for value in values if value is not None]
                if aggregate.function == "count":
                    result[aggregate.alias] = len(present)
                elif aggregate.function == "count_distinct":
                    result[aggregate.alias] = len(set(present))
                else:
                    numbers = [value for value in present
                               if isinstance(value, (int, float)) and not isinstance(value, bool)]
                    if not numbers:
                        result[aggregate.alias] = None
                    elif aggregate.function == "sum":
                        result[aggregate.alias] = sum(numbers)
                    elif aggregate.function == "avg":
                        result[aggregate.alias] = sum(numbers) / len(numbers)
                    elif aggregate.function == "min":
                        result[aggregate.alias] = min(numbers)
                    elif aggregate.function == "max":
                        result[aggregate.alias] = max(numbers)
            output.append(result)
        return output

    @staticmethod
    def _order_grouped(spec: QuerySpec, rows: List[dict]) -> List[dict]:
        ordered = list(rows)
        for clause in reversed(spec.order_by):
            field = clause["field"]
            reverse = clause["direction"] == "desc"
            present = [row for row in ordered if row.get(field) is not None]
            missing = [row for row in ordered if row.get(field) is None]
            present.sort(key=lambda row: row[field], reverse=reverse)
            ordered = present + missing
        return ordered

    @staticmethod
    def _rank(spec: QuerySpec, rows: List[dict]) -> List[dict]:
        if not spec.rank:
            return rows
        path = spec.rank["by"]
        reverse = spec.rank.get("order", "desc") == "desc"

        def key(row):
            value = _resolve(path, row)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return (0, float(value))
            return (1, math.inf if reverse else -math.inf)

        return sorted(rows, key=key, reverse=reverse)

    def _project(self, spec: QuerySpec, row: dict) -> dict:
        if spec.return_fields:
            projected = {}
            for path in spec.return_fields:
                value = _resolve(path, row)
                if isinstance(value, dict) and "geometry" in value and not spec.include_geometry:
                    value = {k: v for k, v in value.items() if k != "geometry"}
                projected[path] = value
            return projected
        if spec.group_aggregates:
            return {key: value for key, value in row.items() if not key.startswith("_")}
        node = row[spec.start_type]
        projected = public_node(node, include_geometry=spec.include_geometry)
        for key, value in row.items():
            if key in {spec.start_type, "id"}:
                continue
            if isinstance(value, list):
                projected[f"{key}_count"] = len(value)
            elif isinstance(value, (int, float, str, type(None))):
                projected[key] = value
            elif isinstance(value, dict):
                projected[key] = value
        return projected
