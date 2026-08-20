# City2Graph — evaluated, not used

The project started around [City2Graph](https://city2graph.net/), and GTFS is the natural place to
ask whether it should carry the transit work. It was evaluated for V0.4 and **is not used**. This
page records why, so the question does not need re-asking from scratch.

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

## If it comes back

The domain model is ours and the source is behind an adapter (`app/transit_sources/base.py`), so a
City2Graph-backed source could be added later without touching routing, the API or the UI. It would
be a reasonable fit for a *different* question — describing the network's structure, frequency
analysis, or feeding a graph learning experiment — rather than for answering "what can I reach by
14:45?".
