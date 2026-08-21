# Architecture

```text
                 PREPARE (once, explicit)                    SERVE (every start)
                 ─────────────────────────                   ───────────────────
OpenStreetMap ──> street_sources/osmnx_source.py ──> basel_walking_network.graphml
                    graph_from_place("walk")        ──> basel_cycling_network.graphml
                    graph_from_place("bike")                        │
                    MultiDiGraph -> undirected                      │
data.bs.ch ─────> ingest.fetch_entities() ────────> basel_entities.json
                    paginate, normalize                             │
                                                                    │
data.bs.ch  ┐                                                       │
OpenStreet- ├───> service_sources/*  ──> ServiceLocation ──┐        │
map         ┘      SOURCE_PLAN per category                │        │
                                                           v        │
                          service_index.snap_services() ───┴──> basel_services.json
                            once per network                        │   (+ walk & bike
                            point -> nearest node                   │    access nodes
                            area  -> nearest outline point          │    + fingerprints)
                                                                    │
opentransport ──> transit_sources/swiss_gtfs.py ──────────> basel_transit.npz
data.swiss         stream 2.9 GB, keep the Basel box                │
                                                                    │
                          data_quality.build_report() ───> data_quality.json
                                                                    │
                                                                    v
                                              app/main.py loads every cache
             ┌─────────────┬──────────────┬──────────────┬─────────────┐
             v             v              v              v             v
       entity graph  StreetNetwork  StreetNetwork  ServiceIndex   TransitIndex
        (graph.py)      "walk"          "bike"    (per-network    (RAPTOR +
                                                  access points)   stop access)
             └────────ACCESS_POINT────────┴──────────────┘             │
                             │                                         │
        ┌────────────────────┼────────────────────┬────────────────────┘
        v                    v                    v
  WalkingAccessibility  CyclingAccessibility  MultimodalAccessibility
   Dijkstra/length_m     Dijkstra/length_m     walk → RAPTOR → walk
        └────────────────────┴────────────────────┘
                             │
                             v
              /accessibility?mode=…  ·  /accessibility/compare
                             │
                             v
                  FastAPI GeoJSON + MapLibre UI
```

## The prepare / serve split

`python -m app.prepare_data` is the only code path that talks to the internet. It downloads, normalizes,
projects, snaps and caches. `uvicorn` reads the caches and nothing else, so startup is ~1.8 s and a
`--reload` costs nothing. The caches themselves are committed as a frozen snapshot (see
[DATA.md](DATA.md)), which makes preparation a *refresh* mechanism rather than a prerequisite —
without weakening the split, because the server still only ever reads prepared files. This is what makes the graph affordable: 14k nodes, 19k edges and 1,308
snapped services are parsed once and then queried thousands of times from memory.

## Source adapters

Two parallel adapter packages, same shape: a `base.py` contract, one module per provider, a cache
module, and an `__init__.py` that resolves a source and degrades explicitly when it cannot.

```text
app/street_sources/                     app/service_sources/
  base.py           StreetNetwork +       base.py            ServiceSource contract,
                    WalkingNetworkSource                     id/name normalization, dedupe
  osmnx_source.py   OSM walking graph     basel_open_data.py schools, sport, culture, clinics
  fixture_source.py synthetic grid        osm_source.py      groceries, pharmacies, doctors,
  graphml_cache.py  normalized cache                         parks, libraries
  __init__.py       load_street_network() fixture_source.py  deterministic synthetic services
                                          cache.py           basel_services.json + fingerprint
                                          __init__.py        SOURCE_PLAN, fetch_services(),
                                                             load_services()
```

Nothing downstream knows where a network came from. `StreetNetwork` guarantees the same shape either way:
undirected graph, nodes with `lon`/`lat` and projected `x`/`y`, edges with a positive `length_m` and a
WGS84 `geom`. Its constructor normalizes: it fills projected coordinates, rebuilds missing edge lengths
from geometry, and drops edges that still have no usable length (reported as `dropped_edges`).

Reading the cache deliberately depends only on networkx and shapely, so the running server does not need
OSMnx (or geopandas, or a network connection) installed at all.

## One question, three modes

`app/modes.py` holds the whole mode vocabulary: the `TravelMode` enum, which prepared network each
mode routes on, its label and its colour. Everything else reads from there.

```text
TravelMode.WALK     → network "walk"   → NetworkAccessibilityService @ 4.8 km/h
TravelMode.BIKE     → network "bike"   → NetworkAccessibilityService @ 15 km/h
TravelMode.TRANSIT  → network "walk"   → MultimodalAccessibilityService + timetable
```

Walking and cycling are the *same engine* with a different graph and speed —
`CyclingAccessibilityService` is nine lines. Transit is a separate service because its cost model is
genuinely different (time of day matters), but it deliberately returns the same shape: origin,
budget, mode, reachable services by category, nearest per category, completeness, provenance. That
is what makes `/accessibility/compare` a table rather than three special cases.

Mode-specific detail lives in mode-specific fields — `network` for the street modes, `transit` and
`journey` for transit — so a client can read the common part and ignore the rest.

## Two products, one pipeline

Everything above builds the **reference application**: the 15-Minute Basel map and its APIs. A
second track now reads the same prepared artefacts and does something different with them.

```text
prepared caches
  basel_walking_network.graphml  ┐
  basel_cycling_network.graphml  │
  basel_entities.json            ├──> reference application  ──> /accessibility, the map
  basel_services.json            │      routing engines           "what can I reach from here?"
  basel_transit.npz              │
  basel_population.json          │
                                 └──> Spatial Graph Core    ──> /spatial-graph/*
                                        typed heterogeneous      "how do these things relate,
                                        graph + query layer       and what does that imply?"
                                              │
                                              └── calls the routing engines for
                                                  anything parameterised by mode/time
```

The Spatial Graph Core is deliberately *additional*. It does not replace the routing structures and
does not copy them — see [SPATIAL_GRAPH.md](SPATIAL_GRAPH.md). Its own module layout:

FastAPI, the CLI and MCP are peers over the same service layer:

```text
Map / FastAPI       CLI          AI client
      │              │               │
      └──────────────┼────────────── MCP
                     │               │
                 SpatialGraphService
                  ↙              ↘
          typed graph         routing analytics
                  ↘              ↙
                    provenance
```

`app/mcp/tools.py` delegates directly to `SpatialGraphService`;
`app/mcp/server.py` only registers those functions with FastMCP. MCP never calls
FastAPI over HTTP and never reimplements graph or routing logic. FastMCP is an
optional Python 3.10+ dependency, preserving the established Python 3.9 app.

```text
app/spatial_graph/
  schema.py       node types, relation types, operators, analyses — machine-readable
  model.py        NetworkXSpatialGraph + the four-method store interface
  builder.py      builds the typed graph from prepared artefacts
  query.py        the bounded query language: parse, validate, execute
  analysis.py     the bridge to the routing engines, memoized
  provenance.py   observed / official / derived / dynamic
  questions.py    the standing cross-domain questions
  export.py       CSV and Cypher, for Neo4j / DuckDB / pandas
  cli.py          query it without FastAPI or a map
  fixtures.py     a fully synthetic graph, for tests
```

The query executor has two compatible aggregate paths. Original object-form
per-start-row aggregates remain intact. List-form aggregates run after
filter/traversal/analysis as typed GROUP BY → HAVING → ORDER BY operations.

## Where the service logic lives

- `app/service_model.py` — `ServiceCategory` enum, `ServiceLocation`, labels, colours, the six
  essential categories. No I/O, no dependencies beyond the standard library.
- `app/service_index.py` — snapping (`snap_services`), the in-memory `ServiceIndex`, the
  reachability query and the completeness indicator.
- `app/analysis.py` — `CityAnalysis`: the inverted, city-wide gap query.
- `app/modes.py` — the travel-mode vocabulary.
- `app/transit_model.py` — the normalized timetable, GTFS time handling, per-service-day views.
- `app/transit_index.py` — RAPTOR, and how stops attach to the walking network.
- `app/multimodal.py` — walk → wait → ride → transfer → walk, and itinerary reconstruction.
- `app/data_quality.py` — the generated report behind `/data/status`.

`WalkingAccessibilityService` gained one constructor argument (`services`) and one method
(`route_to_service`). The Dijkstra engine from V0.2 is untouched; services are a lookup layered on
top of its cost map.

## Two graphs, on purpose

The entity graph stores reusable facts and structural derived relations (`IN_AREA`, `ADJACENT_TO`, `NEAR`,
`ACCESS_POINT`). The street network is optimized for weighted path calculations. Reachability is computed
per request and never materialized as thousands of `REACHABLE` edges.

Only the street nodes that are actually used as access points are copied into the entity graph — mirroring
a 14,102-node city network there would help nobody.

Services are a third structure, deliberately not merged into either: they need a category-typed model
and a fast `access node -> services` lookup, neither of which the entity graph provides.

## Coordinates

Geometry is stored and returned in EPSG:4326. Every distance — snapping, edge lengths, buffers, the
comparison circle — is computed in **EPSG:2056 (CH1903+ / LV95)**, the official Swiss projected CRS.
Nodes carry their projected `x`/`y` so that nearest-node search is a vectorized NumPy `argmin` over a
pre-built array (~50 µs against the full Basel network) instead of a degree-space approximation. Batch
snapping is chunked, so attaching a thousand services never allocates a hundred-megabyte matrix.

## Performance

| Step | Cost |
|---|---|
| `prepare_data` (cold, everything incl. the 224 MB GTFS download) | ~4 min |
| `prepare_spatial_graph` (from prepared artefacts) | ~1.3 s |
| Spatial graph load at startup (4,034 nodes, 4.3 MB JSON) | ~0.04 s |
| Structural graph query (no routing) | ~1 ms |
| Query with a live accessibility constraint, 21 neighbourhoods | ~35 ms |
| A standing question across all neighbourhoods and three modes | ~6 s |
| `prepare_data` (cold, GTFS archive already downloaded) | ~90 s |
| `prepare_data` (warm, all caches valid) | ~15 s |
| Server startup (five caches, index builds, stop + entity attach) | ~1.8 s |
| Walking, 15 min | ~150 ms |
| Cycling, 15 min (5,595 edges — a bicycle covers 3.75 km) | ~420 ms |
| Transit, 15 min | ~210 ms |
| Transit, 30 min | ~250 ms |
| Mode comparison, all three | ~220 ms |
| Route/itinerary to one service | 1–250 ms |
| City-wide gap query (first call builds the node→neighbourhood index) | ~400 ms / ~80 ms after |

The map draws the reachable corridor as a wide translucent line rather than requesting a buffered polygon,
which gives the same picture with none of the GEOS cost.

Three things keep the added modes cheap. The first transit query of a given date pays ~0.4 s to
materialize that service day's trips (which trips run, with after-midnight runs shifted into place);
every later query on that date reuses it. Edge GeoJSON is converted and rounded once per edge and
memoized, because the same edges come back on every query. And the large responses bypass FastAPI's
`jsonable_encoder`, which costs more than the JSON encoding itself on a two-megabyte GeoJSON body.

Each category row carries `ids` — every reachable service — alongside `items`, the detailed rows,
which are capped at 60 per category. The map highlights from `ids`; the sidebar reads `items`. A
15-minute cycling query reaches over a thousand services, and the difference is a megabyte.

Service reachability costs almost nothing because it is a lookup, not a search: the index keeps an
`access node -> services` dictionary, so a query walks the reachable nodes it already has. Snapping
— the genuinely expensive part, 1,308 services against 14,102 nodes — happens once in
`prepare_data` and is cached with the network fingerprint that produced it. The frontend loads the
1,308 service points once (~350 KB) and then re-filters them client-side per click; it never
re-downloads a catalogue per query.

FastAPI serves the static frontend, so V0.2 still needs no Node build system.
