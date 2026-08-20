# City2Graph — evaluated twice, not yet used

The project started around [City2Graph](https://city2graph.net/), so "should this do the work?" is a
fair question to keep asking. It has now been evaluated twice, for two genuinely different jobs:

1. **V0.4 — schedule-aware transit routing.** Rejected: the library has no schedule-aware routing
   and no waiting model. That evaluation says nothing about the rest of the library.
2. **P1 — heterogeneous spatial graph construction.** This is what City2Graph is actually built
   for, so it was re-evaluated properly, with an isolated environment and a measured comparison
   against our own output.

Both verdicts are "not used", but for entirely different reasons, and the second one is much
closer to a "yes".

## What it offers

City2Graph's transportation module does have GTFS support:

| Function | What it does |
|---|---|
| `load_gtfs(path)` | Imports a GTFS zip into DuckDB, materializing stop coordinates as geometry |
| `get_od_pairs(con, …)` | Consecutive stop pairs from trip sequences, with departure/arrival timestamps and travel time in seconds |
| `travel_summary_graph(con, …)` | Aggregates services into a weighted stop-to-stop network — average travel time and frequency per edge — as GeoDataFrames or a NetworkX graph |

That is a genuinely useful set of tools for *describing* a transit network.

## Why it does not fit this milestone

**1. It aggregates schedules; we need to obey them.** `travel_summary_graph` produces
service-weighted *average* travel times between stops. V0.4's whole point is that a 14:31 departure
and a 14:39 departure give different answers, and that waiting for the next vehicle is real time.
An averaged edge weight cannot express "you just missed it". There is no schedule-aware routing, no
isochrone and no waiting-time model in the API.

**2. Python version.** City2Graph 1.0.0 requires Python ≥ 3.12; this project runs on the 3.9
interpreter the repository was set up with. Adopting it would mean moving the whole project's
Python version for a library we would then only use for CSV parsing.

**3. Dependency weight.** It pulls in DuckDB, PyTorch Geometric, momepy, libpysal, rustworkx and
overturemaps — a GNN/GeoAI stack. V0.4 explicitly excludes GNNs, and the transit work needs none of
it. Our extractor is one streaming pass over the archive with no new dependency at all.

## What we did instead

Schedule-aware routing implemented directly, in about 400 lines:

- `app/transit_sources/swiss_gtfs.py` — streaming extraction of the Basel subset;
- `app/transit_model.py` — the normalized timetable, GTFS time handling, per-service-day views;
- `app/transit_index.py` — RAPTOR, and how stops attach to the walking network.

## Evaluation 2 (P1) — heterogeneous graph construction

This time the library was actually installed and run against our data. Python 3.14 satisfies its
`>=3.12,<3.15` requirement, and — importantly — **torch is only an extra**, not a base dependency,
so the install is far lighter than the V0.4 evaluation assumed.

```bash
python3.14 -m venv .c2g && .c2g/bin/pip install city2graph
python -m app.spatial_graph.cli export experiments/export     # project interpreter
python -m experiments.city2graph_spike --export               # project interpreter
.c2g/bin/python experiments/city2graph_spike.py               # spike interpreter
```

### What it reproduced

Given the same 21 neighbourhood polygons, 1,308 services and 283 stops:

| Our construction | City2Graph equivalent | Ours | City2Graph | |
|---|---|---:|---:|:--|
| `ADJACENT_TO` (shapely boundary contact) | `contiguity_graph(queen)` | 36 pairs | 36 | ✅ exact |
| `HAS_SERVICE` (STRtree point-in-polygon) | `group_nodes(polygons, points)` | 1,238 | 1,238 | ✅ exact |
| `HAS_TRANSIT_STOP` | `group_nodes(polygons, points)` | 228 | 228 | ✅ exact |

In 56 ms, 20 ms and 7 ms respectively — comparable to ours. `gdf_to_nx` then converts cleanly to a
NetworkX graph, carrying our properties through.

**It works.** This is not a capability gap.

### What it would add that we do not have

`bridge_nodes` (kNN / Delaunay / fixed-radius proximity layers between node types), `add_metapaths`,
`create_tessellation`, `morphological_graph`, and `gdf_to_pyg` for PyTorch Geometric. None of these
is needed by P1, and two of them (tessellation, morphology) are exactly what a future
building-level or block-level milestone would want.

### Why it is still not used

**1. It would set the project's Python floor to 3.12.** The repository runs on 3.9. Upgrading the
whole project — including the OSMnx/GTFS pipeline, which currently works — to gain two functions is
a poor trade. The alternative, building the graph in one interpreter and serving it from another,
adds a moving part to a pipeline whose main virtue is that it has few.

**2. The two constructions it replaces are ~60 lines.** `_add_adjacency` and `_containing_area` in
`app/spatial_graph/builder.py` are short, dependency-free shapely, and they were *verified correct*
by this very comparison.

**3. Its output loses our domain semantics.** `group_nodes` returns node layers named `polygon` and
`point`, with edge type `('polygon', 'covers', 'point')`. Our schema needs `Neighborhood`,
`ServiceLocation`, `HAS_SERVICE`, each edge carrying `derived` and `method` for provenance. We would
map straight back out of its vocabulary into ours.

**4. It does not touch the parts that were actually hard.** Attaching entities to two routing
networks with per-network snap quality, the population time series, provenance classification, the
query layer, the analytical bridge — none of that is in scope for a graph-construction library, and
that is where the work of this milestone went.

### When to revisit

Adopt it when the graph needs something it does better than we ever will:

- **tessellation or morphological graphs** — a building- or block-level milestone;
- **proximity layers** (`bridge_nodes`, kNN, Delaunay) as first-class relations;
- **PyTorch Geometric export**, if a GNN milestone ever happens — `gdf_to_pyg` is the cleanest path;
- **a Python upgrade for other reasons** — at that point the cost is already paid.

The spike is kept in [`experiments/city2graph_spike.py`](../experiments/city2graph_spike.py) so
re-running the comparison is a two-minute job rather than a fresh investigation.
