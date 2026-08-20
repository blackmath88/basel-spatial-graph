# The query API

A query is a validated JSON document. It is not Cypher, not SQL and
not Python — nothing a caller sends is ever interpreted as code, and every field
name, relation and operator is validated against
[the schema](SPATIAL_GRAPH.md) before anything runs.

```bash
curl -X POST localhost:8000/spatial-graph/query \
     -H 'Content-Type: application/json' \
     -d @examples/queries/children_pharmacy_transit.json

python -m app.spatial_graph.cli query examples/queries/children_pharmacy_transit.json
```

## The grammar

```jsonc
{
  "start":     { "type": "Neighborhood", "filters": [...], "ids": [...] },
  "traverse":  [ { "relation": "...", "target_type": "...", "as": "...", "from": "..." } ],
  "analyses":  [ { "analysis": "accessibility", "mode": "walk", "minutes": 15 } ],
  "group_by":  [ "year" ],
  "aggregate": [ { "function": "sum", "field": "children", "as": "children_total" } ],
  "having":    [ { "field": "children_total", "op": "gt", "value": 10000 } ],
  "order_by":  [ { "field": "year", "direction": "asc" } ],
  "rank":      { "by": "path", "order": "asc" },
  "return":    [ "Neighborhood.name", "pharmacies.count" ],
  "limit": 50,
  "include_geometry": false
}
```

Only `start` is required.

### `start`

Which node type to begin from, optionally narrowed.

```json
{"start": {"type": "Neighborhood",
           "filters": [{"field": "children", "op": "gt", "value": 1400}]}}
```

`ids` restricts to specific entities instead of scanning the type.

### Filters

| Operator | Meaning |
|---|---|
| `eq`, `ne` | equals, not equals |
| `gt`, `gte`, `lt`, `lte` | numeric comparison |
| `in`, `not_in` | value is (not) in a list |
| `between` | within `[low, high]` |
| `contains` | case-insensitive substring |
| `exists` | field is present and not null |

A field that the type does not declare is an error, and the error lists the
fields that do exist — a caller can recover without reading the source.

### `traverse`

Follow a typed relation. Each step names its result with `as`, and later steps
can chain from an earlier one with `from`.

```json
"traverse": [
  {"relation": "HAS_TRANSIT_STOP", "target_type": "TransitStop", "as": "stops"},
  {"relation": "SERVED_BY", "target_type": "TransitRoute", "as": "routes", "from": "stops",
   "filters": [{"field": "vehicle", "op": "eq", "value": "Tram"}]}
]
```

`min_count` / `max_count` turn a step into a constraint on the starting row:
`{"relation": "HAS_SERVICE", "min_count": 1}` keeps only neighbourhoods that
contain at least one service.

At most four steps. **Analytical relations cannot be traversed** — asking to
traverse `REACHABLE_WITHIN` is refused, with a pointer to `analyses` instead.

### `analyses` — calling the routing engines

This is the part that makes the layer more than a POI database. An analysis runs
a real accessibility computation per row and puts the result in the row context.

```json
"analyses": [
  {"as": "pharmacy_walk_15", "analysis": "accessibility",
   "mode": "walk", "minutes": 15, "target_category": "pharmacy",
   "operator": "count_lte", "value": 1}
]
```

| Parameter | |
|---|---|
| `mode` | `walk`, `bike`, `transit` |
| `minutes` | 1–60 |
| `target_category` | a category id, or omit for all categories |
| `departure_time` | transit only; `HH:MM` or ISO, Europe/Zurich |
| `max_transfers` | transit only |

The optional constraint filters rows. Two equivalent forms:

```jsonc
"operator": "count_lte", "value": 1                       // shorthand
"constraint": {"field": "count", "op": "lte", "value": 1} // explicit
```

Results expose `count`, `nearest_minutes`, `nearest_name`, `completeness`,
`per_category`, `origin` and `kind`. `kind` is always
`"dynamic analytical computation"` — these numbers were computed for *these*
parameters and would differ for others.

At most four analyses per query. Results are memoized per (neighbourhood, mode,
minutes, departure), and every response reports how many engine calls it made.

### `group_by`, grouped `aggregate`, `having`, `order_by`

Grouped aggregation consumes the filtered/traversed row stream and produces one
row per unique combination of grouping keys:

```json
{
  "start": {"type": "PopulationObservation"},
  "group_by": ["year"],
  "aggregate": [
    {"function": "sum", "field": "children", "as": "children_total"},
    {"function": "avg", "field": "total", "as": "neighborhood_average"}
  ],
  "having": [{"field": "children_total", "op": "gt", "value": 10000}],
  "order_by": [{"field": "year", "direction": "asc"}],
  "return": ["year", "children_total", "neighborhood_average"]
}
```

Functions are `count`, `count_distinct`, `sum`, `avg`, `min`, and `max`.
Multiple grouping keys are supported. Aggregate aliases are required and may
be referenced by `having`, `order_by`, and `return`. Fields are checked against
the typed schema before execution; numeric aggregates reject non-numeric fields
and report valid alternatives.

`count` without a field counts rows; with a field it ignores null/missing
values. Numeric aggregates ignore null/missing values and return `null` when a
group has no numeric values. Scalar outputs from dynamic analyses can be
grouped; nested `per_category` structures cannot.

Examples live in `examples/queries/population_by_year.json`,
`services_by_category.json`, `transit_stops_by_neighborhood.json`, and
`top_service_neighborhoods.json`.

The execution trace includes rows scanned, grouping keys, computations, groups
formed/returned, HAVING, and ordering. Provenance includes the source datasets
and a compact `aggregation` computation block.

### Legacy per-row `aggregate`, `rank`, `return`, `limit`

```json
"aggregate": {"service_count": {"op": "count", "of": "services"}},
"rank": {"by": "service_count", "order": "desc"},
"return": ["Neighborhood.name", "Neighborhood.children", "pharmacies.count"],
"limit": 25
```

Existing object-form aggregate and `rank` queries remain valid. Ops are
`count`, `sum`, `avg`, `min`, `max`. Paths resolve against the row —
`Neighborhood.name`, `pharmacies.count`, `walk15.nearest_minutes`. Omit `return`
and you get the whole start node plus a `<name>_count` per traversal.

**Results are always bounded**: default 50, hard maximum 1,000, and `truncated`
says when more matched.

**Geometry is excluded by default.** Set `include_geometry: true` when you want
it — a future MCP client should not be handed megabytes of GeoJSON it did not
ask for.

## What comes back

```jsonc
{
  "results": [ ... ],
  "count": 3,
  "truncated": false,
  "execution": {
    "start_type": "Neighborhood",
    "candidates_scanned": 11,
    "filters_applied": 1,
    "relations_traversed": [],
    "analyses": [{"as": "pharmacy_walk_15", "type": "accessibility",
                  "mode": "walk", "minutes": 15, "category": "pharmacy"}],
    "analysis_calls": 11,
    "elapsed_seconds": 0.035
  },
  "provenance": {
    "datasets": [ {"source": "data.bs.ch", "dataset": "100042", "license": "..."} ],
    "relations_traversed": [ {"relation": "HAS_SERVICE", "persisted": true} ],
    "analyses": [ {"type": "accessibility", "classification": "dynamic"} ],
    "origin_method": "The polygon's representative point ...",
    "population_reference_year": 2025,
    "classification_key": { "observed": "...", "official": "...",
                            "derived": "...", "dynamic": "..." }
  },
  "query": { ...the parsed specification, echoed back... }
}
```

`execution` says what the engine did; `provenance` says where the numbers came
from and which of them were computed live. Both exist so an answer can be
audited without reading the code.

## Discovery

```text
GET /spatial-graph/schema           everything: types, fields, relations, operators, analyses
GET /spatial-graph/entity-types     node types with live counts
GET /spatial-graph/relation-types   relations, and whether each is persisted
GET /spatial-graph/questions        the standing cross-domain questions
```

## Retrieval

```text
GET /spatial-graph/entities/{type}
GET /spatial-graph/entities/{type}/{id}
GET /spatial-graph/entities/{type}/{id}/neighbors?relation=&target_type=
GET /spatial-graph/entities/{type}/{id}/subgraph?depth=2&relations=
GET /spatial-graph/provenance/{entity_or_relation}
GET /spatial-graph/status
```

## Standing questions

Six cross-domain questions ship as functions, so the thresholds are arguments
rather than opinions buried in code. Each answer carries its own `methodology`.

```bash
python -m app.spatial_graph.cli ask                      # list them
python -m app.spatial_graph.cli ask q1_poorest_access --table
curl 'localhost:8000/spatial-graph/questions/q6_children_underserved'
```

| Question | Asks |
|---|---|
| `q1_poorest_access` | poorest access to one category, by mode |
| `q2_schools_vs_healthcare` | good schools, poor healthcare |
| `q3_adjacent_contrasts` | which neighbours differ most |
| `q4_category_inequality` | which categories are most unevenly spread |
| `q5_mode_gain` | who gains most from cycling or transit |
| `q6_children_underserved` | many children, poor pharmacy *and* transit access |

Where a question uses a threshold, it defaults to the **median of the observed
Basel distribution** and returns the thresholds it used, so the rule can be
checked or changed rather than taken on trust.

## Limitations

- **No OR between filters.** Filters are conjunctive.
- **No arbitrary joins or expressions.** Traversal follows declared relations;
  aggregate inputs are fields, not formulas.
- **Traversal is forward-only** along declared relations; use the declared
  inverse to go the other way.
- **Analyses only apply to `Neighborhood`**, because that is the only type with
  a defensible representative origin.
- **No pagination on `/query`** — use `limit` and narrow the filters.
