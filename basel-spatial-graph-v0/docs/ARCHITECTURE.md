# Architecture

```text
                 PREPARE (once, explicit)                    SERVE (every start)
                 ─────────────────────────                   ───────────────────
OpenStreetMap ──> street_sources/osmnx_source.py ──> basel_walking_network.graphml
                    graph_from_place("walk")                        │
                    MultiDiGraph -> undirected                      │
                                                                    │
data.bs.ch ─────> ingest.fetch_entities() ────────> basel_entities.json
                    paginate, normalize                             │
                                                                    │
data.bs.ch  ┐                                                       │
OpenStreet- ├───> service_sources/*  ──> ServiceLocation ──┐        │
map         ┘      SOURCE_PLAN per category                │        │
                                                           v        │
                          service_index.snap_services() ───┴──> basel_services.json
                            point -> nearest node                   │   (+ access nodes
                            area  -> nearest outline point          │    + net fingerprint)
                                                                    │
                          data_quality.build_report() ───> data_quality.json
                                                                    │
                                                                    v
                                              app/main.py loads all three caches
                              ┌──────────────────┬──────────────────┐
                              v                  v                  v
                        entity graph       StreetNetwork      ServiceIndex
                         (graph.py)     (street_sources)   (service_index.py)
                              └────────ACCESS_POINT────────┘        │
                                              │                     │
                                              v                     v
                                       WalkingAccessibilityService  CityAnalysis
                                        Dijkstra by length_m        multi-source Dijkstra
                                              │                     │
                                              v                     v
                                       FastAPI GeoJSON + MapLibre UI
```

## The prepare / serve split

`python -m app.prepare_data` is the only code path that talks to the internet. It downloads, normalizes,
projects, snaps and caches. `uvicorn` reads the caches and nothing else, so startup is ~1.3 s and a
`--reload` costs nothing. This is what makes the graph affordable: 14k nodes, 19k edges and 1,308
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

## Where the service logic lives

- `app/service_model.py` — `ServiceCategory` enum, `ServiceLocation`, labels, colours, the six
  essential categories. No I/O, no dependencies beyond the standard library.
- `app/service_index.py` — snapping (`snap_services`), the in-memory `ServiceIndex`, the
  reachability query and the completeness indicator.
- `app/analysis.py` — `CityAnalysis`: the inverted, city-wide gap query.
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
| `prepare_data` (cold: network + entities + 1,308 services + snapping) | ~35 s |
| `prepare_data` (warm, all caches valid) | ~4 s |
| Server startup (three caches, index build, entity attach) | ~1.3 s |
| 15-minute query incl. services, 2,362 edges | ~30 ms |
| 15-minute service profile only (`/accessibility/walk/services`) | ~15 ms |
| Route to one service | ~10 ms |
| City-wide gap query (first call builds the node→neighbourhood index) | ~400 ms / ~80 ms after |
| 15-minute query with `include_buffer=true` | ~0.9 s (GEOS buffering; opt-in) |

The map draws the reachable corridor as a wide translucent line rather than requesting a buffered polygon,
which gives the same picture with none of the GEOS cost.

Service reachability costs almost nothing because it is a lookup, not a search: the index keeps an
`access node -> services` dictionary, so a query walks the reachable nodes it already has. Snapping
— the genuinely expensive part, 1,308 services against 14,102 nodes — happens once in
`prepare_data` and is cached with the network fingerprint that produced it. The frontend loads the
1,308 service points once (~350 KB) and then re-filters them client-side per click; it never
re-downloads a catalogue per query.

FastAPI serves the static frontend, so V0.2 still needs no Node build system.
