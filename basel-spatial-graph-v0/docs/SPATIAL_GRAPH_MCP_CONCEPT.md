# Spatial Graph Core — the concept

*Exploratory. This records the direction and the open questions, not a set of
commitments. Where a P1 experiment has since answered one of them, the answer is
marked **▸ finding**; those are evidence from
[docs/SPATIAL_GRAPH.md](SPATIAL_GRAPH.md), not decisions made in advance.*

## Where the project is coming from

Four milestones built a reference application that proves a pipeline:

```text
open data → GIS normalization → network computation → API → interactive map
```

It answers one question extremely well: *what can I reach from this point, by
this mode, in this many minutes?* Everything in it is arranged around that
question — the routing graphs, the service index, the timetable, the map.

The limitation is not accuracy. It is that the question has to be asked from a
map click, one origin at a time, and the answer comes back as geometry. If you
want to ask *which neighbourhoods with many children have poor pharmacy and
transit access*, there is nowhere to put that question.

## Where it is going

```text
spatial datasets attached to routing networks
        ↓
a heterogeneous typed graph that can be queried compositionally
```

The map becomes one client among several. The thing being built is an
intelligence layer: heterogeneous spatial and statistical data, relationally
queryable, with provenance attached, usable by a person, a script, a report, or
eventually an agent.

## The core idea

City data is naturally a heterogeneous graph — many node types, many edge types:

```text
Neighborhood ──HAS_SERVICE──> ServiceLocation ──OF_CATEGORY──> ServiceCategory
     │                              │
     ├──ADJACENT_TO──> Neighborhood └──ACCESS_POINT──> StreetAccessPoint
     ├──HAS_TRANSIT_STOP──> TransitStop ──SERVED_BY──> TransitRoute
     └──HAS_POPULATION_OBSERVATION──> PopulationObservation
```

Once that exists, questions become traversals, and traversals compose.

## Open questions this milestone was meant to probe

**Should the routing graphs and the analytical graph be one thing?**
No — they are optimized for different work. Keep them separate and let the
analytical graph *reference* the routing engines.
▸ finding: separation held up; `StreetAccessPoint` is the seam.

**Which relations should be persisted and which computed?**
Structural facts persist. Anything parameterised by mode, time budget or
departure time cannot: there would be millions of edges and they would be stale
the moment a parameter changed.
▸ finding: this is the single most useful distinction in the design, and it is
now in the schema itself, so a client can see which is which.

**Storage: NetworkX? DuckDB? Neo4j?**
Start with the least infrastructure and find out.
▸ finding: at 4,034 nodes NetworkX is enough, and DuckDB is within ~2× on the
same queries — so *performance* is not the deciding factor at this size.
Ergonomics might become one: see the GROUP BY finding in
[SPATIAL_GRAPH.md](SPATIAL_GRAPH.md#architectural-findings).

**City2Graph: does it belong here?**
It was rejected for schedule-aware transit in V0.4. That says nothing about
heterogeneous graph construction, which is what it is actually for.
▸ finding: re-evaluated empirically. It reproduces our adjacency and containment
edges exactly. Not adopted for P1 — the reason is Python version and
domain-semantics cost, not capability. See [CITY2GRAPH.md](CITY2GRAPH.md).

**A query DSL, or something existing?**
Arbitrary Cypher/SQL/Python from an untrusted caller is not an option. A small
bounded language might be enough, or might be a trap.
▸ finding: small is working, and its *limits* are informative — the first thing
it could not express (GROUP BY over node fields) became the focused P2 extension.

## Where MCP comes in

An agent should discover the graph, not be told about it in a prompt. That is
why schema discovery is an API endpoint rather than a README section. The
intended tools map onto functions that already exist:

| MCP tool | Core capability |
|---|---|
| `describe_graph` | `GET /spatial-graph/schema` |
| `query_graph` | `POST /spatial-graph/query` |
| `find_reachable` | `GET /accessibility?mode=…` |
| `compare_areas` | `GET /accessibility/compare`, `q3_adjacent_contrasts` |
| `get_provenance` | `GET /spatial-graph/provenance/{id}` |

P3 now implements these five tools as a thin FastMCP adapter over the same
Python service functions. A natural-language layer remains deliberately absent:
the MCP client is the model, while this server stays deterministic and auditable.
See [MCP.md](MCP.md).

## What would make this worth continuing

- Cross-domain questions that were previously unaskable become one API call. ✅
- Every answer states its sources, its methodology and which parts were computed
  live. ✅
- The graph can be discovered without reading the code. ✅
- Adding a dataset means adding node and relation types, not rewriting queries.
