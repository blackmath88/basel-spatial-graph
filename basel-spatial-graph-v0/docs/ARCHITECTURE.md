# Architecture

```text
                 PREPARE (once, explicit)              SERVE (every start)
                 ─────────────────────────             ───────────────────
OpenStreetMap ──> street_sources/osmnx_source.py ──┐
                    graph_from_place("walk")       │
                    MultiDiGraph -> undirected     ├─> basel_walking_network.graphml
                    project to EPSG:2056           │        │
                                                   │        v
data.bs.ch ─────> ingest.fetch_entities() ─────────┴─> basel_entities.json
                    paginate, normalize                     │
                                                            v
                                              app/main.py loads both caches
                                                    │              │
                                                    v              v
                                             entity graph    StreetNetwork
                                            (graph.py)      (street_sources/base.py)
                                                    │              │
                                                    └── ACCESS_POINT ┘
                                                            │
                                                            v
                                             WalkingAccessibilityService
                                              Dijkstra weighted by length_m
                                                            │
                                                            v
                                            FastAPI GeoJSON + MapLibre UI
```

## The prepare / serve split

`python -m app.prepare_data` is the only code path that talks to the internet. It downloads, normalizes,
projects and caches. `uvicorn` reads the caches and nothing else, so startup is ~0.9 s and a `--reload`
costs nothing. This is what makes the network graph affordable: 10k nodes and 14k edges are parsed once
and then queried thousands of times from memory.

## Source adapters

```text
app/street_sources/
  base.py            StreetNetwork + WalkingNetworkSource contract + provenance shape
  osmnx_source.py    OpenStreetMap via OSMnx; downloads only when asked to
  fixture_source.py  synthetic grid with a deliberate barrier
  graphml_cache.py   read/write the normalized cache (networkx + shapely only)
  __init__.py        load_street_network(): resolve source, degrade explicitly
```

Nothing downstream knows where a network came from. `StreetNetwork` guarantees the same shape either way:
undirected graph, nodes with `lon`/`lat` and projected `x`/`y`, edges with a positive `length_m` and a
WGS84 `geom`. Its constructor normalizes: it fills projected coordinates, rebuilds missing edge lengths
from geometry, and drops edges that still have no usable length (reported as `dropped_edges`).

Reading the cache deliberately depends only on networkx and shapely, so the running server does not need
OSMnx (or geopandas, or a network connection) installed at all.

## Two graphs, on purpose

The entity graph stores reusable facts and structural derived relations (`IN_AREA`, `ADJACENT_TO`, `NEAR`,
`ACCESS_POINT`). The street network is optimized for weighted path calculations. Reachability is computed
per request and never materialized as thousands of `REACHABLE` edges.

Only the street nodes that are actually used as access points are copied into the entity graph — mirroring
a 10,232-node city network there would help nobody.

## Coordinates

Geometry is stored and returned in EPSG:4326. Every distance — snapping, edge lengths, buffers, the
comparison circle — is computed in **EPSG:2056 (CH1903+ / LV95)**, the official Swiss projected CRS.
Nodes carry their projected `x`/`y` so that nearest-node search is a vectorized NumPy `argmin` over a
pre-built array (~50 µs against the full Basel network) instead of a degree-space approximation.

## Performance

| Step | Cost |
|---|---|
| `prepare_data` (cold) | ~20 s |
| Server startup (cache load, index build, entity attach) | ~0.9 s |
| 15-minute query, 2,362 edges | ~25 ms |
| 15-minute query with `include_buffer=true` | ~0.9 s (GEOS buffering; opt-in) |

The map draws the reachable corridor as a wide translucent line rather than requesting a buffered polygon,
which gives the same picture with none of the GEOS cost.

FastAPI serves the static frontend, so V0.2 still needs no Node build system.
