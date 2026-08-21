# Spatial Graph Core

A second representation of Basel, alongside the routing graphs the reference
application uses. Those answer *how do I get there*. This one answers *how do
these things relate* — and can call the first when a question needs both.

The graph ships prepared in the repository's frozen snapshot, so these work in a
fresh clone with no preparation step:

```bash
python -m app.spatial_graph.cli describe
curl localhost:8000/spatial-graph/schema

python -m app.prepare_spatial_graph      # only to rebuild it from refreshed sources
```

## Why it is separate from the routing graphs

The project already has several structures, each optimized for its job:

| Structure | Optimized for |
|---|---|
| walking network (14,102 nodes) | shortest paths on foot |
| cycling network (5,918 nodes) | shortest paths by bicycle |
| service index (1,308 locations) | "which services hang off this node?" |
| transit index (200,696 trips) | round-based schedule search |
| entity graph | the V0 structural relations |

Merging those into one graph would make every one of them worse. A Dijkstra does
not want typed edges and nested provenance; a cross-domain query does not want
20,000 undifferentiated street nodes.

So the Spatial Graph Core is **additional**, and it *references* the routing
structures rather than copying them. The seam is `StreetAccessPoint`: only the
street nodes that something actually attaches to appear here — 2,063 of them,
not 20,000.

```text
Spatial Graph Core          ─ references ─>   routing engines
typed relations                               shortest paths
multi-hop traversal                           isochrones
cross-domain queries                          schedule search
aggregation, schema, provenance
```

## Node types

| Type | Count | Source |
|---|---:|---|
| `Neighborhood` | 21 | data.bs.ch `100042` — Wohnviertel polygons |
| `PopulationObservation` | 210 | data.bs.ch `100128` — 21 areas × 10 years |
| `ServiceCategory` | 8 | the `ServiceCategory` enum |
| `ServiceLocation` | 1,308 | data.bs.ch + OpenStreetMap |
| `StreetAccessPoint` | 2,063 | OSM walking and cycling networks |
| `TransitStop` | 283 | opentransportdata.swiss |
| `TransitRoute` | 141 | opentransportdata.swiss |

**4,034 nodes, 14,092 edges.**

Only stops that are inside a neighbourhood *or* attached to the pedestrian
network are included — of the 1,437 in the timetable, the rest can be ridden
through but are not Basel entities.

## Relation types

### Persisted — structural facts

| Relation | Count | Meaning |
|---|---:|---|
| `LOCATED_IN` | 1,466 | point entity inside a neighbourhood polygon |
| `HAS_SERVICE` | 1,238 | inverse, for services |
| `HAS_TRANSIT_STOP` | 228 | inverse, for stops |
| `OF_CATEGORY` / `HAS_MEMBER` | 1,308 each | service ↔ its category |
| `ACCESS_POINT` / `ATTACHES` | 2,861 each | entity ↔ street node, per network |
| `SERVED_BY` / `SERVES` | 1,165 each | stop ↔ route |
| `ADJACENT_TO` | 72 | neighbourhood polygons sharing a boundary (36 pairs) |
| `HAS_POPULATION_OBSERVATION` / `OBSERVES` | 210 each | area ↔ its figures for a year |

### Never persisted — analytical

`REACHABLE_WITHIN` is declared in the schema with `persisted: false` and a
`computed_by` pointer. It is a *type*, not a set of edges.

Materialising it would mean 21 neighbourhoods × 1,308 services × 3 modes × 4
budgets ≈ 330,000 edges — for one departure time, going stale the moment any
parameter changed. Instead a query calls the engines:

```json
{"analyses": [{"analysis": "accessibility", "mode": "walk",
               "minutes": 15, "target_category": "pharmacy"}]}
```

Every result says which kind it is (`"kind": "dynamic analytical computation"`),
and the schema endpoint lets a client tell them apart before asking.

## The statistical dimension

The first genuinely statistical dataset in the project: **`100128`,
Wohnbevölkerung nach Geschlecht, Alter, Staatsangehörigkeit und Wohnviertel** —
resident population by *single year of age* per neighbourhood, published
annually. All 21 neighbourhoods, 10 years prepared (2016–2025 of 49 available).

Age groups are aggregated server-side by the source's own API with explicit
boundaries — nothing estimated, interpolated or invented:

| Group | Definition |
|---|---|
| `total` | all residents |
| `children` | 0–17 (minors) |
| `young` | 0–19 (the cantonal *Jugendquotient* definition) |
| `working_age` | 20–64 |
| `elderly` | 65+ (the cantonal *Altersquotient* definition) |
| `elderly_80_plus` | 80+ |

`young` and `elderly` deliberately match Basel-Stadt's own published ratios, so
derived figures line up with the canton's statistics rather than quietly
disagreeing with them.

### Nodes *and* properties — and why both

Both representations were built and the answer is: use each where it is honest.

- **`PopulationObservation` nodes** hold the time series. You can ask for 2019
  without the graph pretending it is now, and the year is a first-class value
  rather than something overwritten on each refresh.
- **The latest year is also denormalized onto `Neighborhood`** as
  `population_total`, `children`, `elderly`, `child_share`, `reference_year`
  and so on.

The denormalization is what keeps the query language small: *"neighbourhoods
with more than 1,400 children"* is a plain field filter rather than a
traversal-plus-aggregate. `reference_year` travels with the values, so a reader
always knows which year they are looking at.

## Representative origins

Comparing accessibility across neighbourhoods needs an origin per neighbourhood,
and a naive centroid can land in the Rhine. The method, stated in every result
that depends on it:

> The polygon's representative point (guaranteed inside the neighbourhood),
> moved to the nearest pedestrian-network node that also lies inside the
> neighbourhood. Not population-weighted: the population data is only available
> per neighbourhood, so there is nothing finer to weight by.

That last clause matters. Population weighting would be better, and it is not
possible with this data — saying so is more useful than implying precision that
is not there.

## Storage

`data/processed/basel_spatial_graph.json` — a NetworkX `MultiDiGraph`,
persisted as plain JSON. 4.3 MB, loads in **0.04 s**.

Why not the alternatives:

- **pickle** — fragile across Python and library versions, and unsafe to load
  from anywhere you do not control. A cache should not be a code-execution
  vector.
- **GraphML** — cannot hold the nested provenance dicts and list attributes
  these nodes carry. Good enough for the routing networks, not for this.
- **DuckDB / Neo4j** — real infrastructure for a 4 MB artefact. Both are
  exportable to (see below); neither is required to run.

Access goes through a four-method interface — `get_node`, `neighbors`,
`traverse`, `find` — so another backend means implementing four methods, not
rewriting the query layer.

## Exporting to other tools

```bash
python -m app.spatial_graph.cli export experiments/export
```

Writes one CSV per node type and per relation type in `neo4j-admin import`
shape (`:ID`, `:LABEL`, `:START_ID`, `:END_ID`, `:TYPE`), a `schema.cypher` with
constraints and indexes, and a `graph.cypher` of idempotent `MERGE` statements.
The same CSVs load straight into DuckDB or pandas. No Neo4j instance required —
the point is to make the later comparison cheap, not to move now.

## Architectural findings

Things the experiments actually changed our mind about.

**NetworkX is sufficient at this size — and DuckDB is not obviously better.**
[`experiments/duckdb_spike.py`](../experiments/duckdb_spike.py) runs the same
three queries both ways on identical data, with DuckDB using materialized tables
(views over CSV re-parse on every query and made DuckDB look 30× worse than it
is):

| Query | DuckDB | NetworkX |
|---|---:|---:|
| filter + join + aggregate | 0.73 ms | 0.57 ms |
| degree ranking | 0.43 ms | 0.24 ms |
| time-series rollup | 0.18 ms | 0.04 ms |
| load | 34 ms | 34 ms |

Same answers, same load time, within ~2×. **Performance is not the deciding
factor at 4,000 nodes.** If DuckDB earns its place later it will be on
ergonomics, not speed.

**Ergonomics is where DuckDB exposed a real gap.** The third query — total
children per year across all neighbourhoods — was one `GROUP BY` in SQL and
previously needed hand-written Python on the NetworkX side. P2 has now closed
that gap with typed grouping, aggregation, HAVING and ordering while retaining
the current store.

**The persisted/dynamic split is the load-bearing idea.** It keeps the graph
at 4 MB instead of hundreds, keeps answers correct when parameters change, and —
because it is declared in the schema — lets a client reason about cost before
asking.

**A custom DSL was the right call, so far.** Six parts, validated against the
schema, no arbitrary strings reaching an engine. Its limits are informative
rather than annoying: the GROUP BY gap above was the first real one, and the DSL
grew specifically to cover it.

**Small graph, large questions.** The interesting cost is not traversal — the
whole graph traverses in under a millisecond. It is the accessibility engine
calls, which is why they are memoized per (neighbourhood, mode, minutes,
departure) and why the query layer reports how many it made.

## City2Graph

Re-evaluated for this use case — properly, in an isolated Python 3.14
environment — and it reproduces our structural constructions exactly. Not
adopted for P1. The full reasoning, with numbers, is in
[CITY2GRAPH.md](CITY2GRAPH.md).

## Limits

- **21 neighbourhoods is a coarse unit.** Every accessibility figure is one
  origin standing for a whole Wohnviertel. Riehen is 11 km²; one point does not
  represent it well.
- **Population is per neighbourhood only**, so nothing is population-weighted
  and no sub-area analysis is possible.
- **No arbitrary joins or derived-field expressions** in the query language;
  grouping follows the validated row stream and declared field paths.
- **`ADJACENT_TO` is polygon contact**, not walkability — two neighbourhoods can
  touch across a motorway.
- **Accessibility counts are not quality.** One kiosk counts as one grocery.
