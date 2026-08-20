# 15-Minute Basel Spatial Graph

V0.2 answers a concrete question: **what can I actually reach by walking through Basel's street network?** Click a map origin, choose 5, 10, or 15 minutes, and see routed street segments, an approximate visual boundary, and reachable schools.

## Run

Requires Python 3.9+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>; API documentation is at <http://127.0.0.1:8000/docs>.

On its first normal start, the app tries official Basel entity datasets and an OpenStreetMap walking-network query. Successful OSM data is cached at `data/processed/basel_walk_network.json`. If either source is unavailable, `/health` and the UI clearly report fixture mode. For an immediate deterministic demo:

```bash
BASEL_GRAPH_FIXTURE=1 uvicorn app.main:app --reload
```

## API examples

```bash
curl 'http://127.0.0.1:8000/accessibility/walk?lat=47.559&lon=7.59&minutes=15'
curl 'http://127.0.0.1:8000/accessibility/walk?lat=47.559&lon=7.59&minutes=5&walking_speed_kmh=4.8'
curl 'http://127.0.0.1:8000/entities/schools/school%3A1/accessibility?mode=walk&minutes=10'
```

## Test

```bash
BASEL_GRAPH_FIXTURE=1 pytest
```

The tests use a tiny deterministic walking graph and do not depend on network services.

## What V0.2 contains

- Basel `Area`, `School`, and `Accident` entities and the earlier `IN_AREA`, `ADJACENT_TO`, and straight-line `NEAR` relations.
- A separately sourced pedestrian street graph with `StreetNode` nodes and length-weighted `WALKABLE_TO` edges.
- Persistent `ACCESS_POINT` links from point entities to their nearest walking-network node.
- Dynamic Dijkstra reachability; no large set of persistent `REACHABLE` edges.
- Sorted reachable schools with network distance and walking time.
- Area intersection summaries and Euclidean-versus-network distance comparisons.
- Reachable street GeoJSON plus a clearly labeled approximate buffered boundary.
- Full source and analytical provenance in each response.

See [the accessibility guide](docs/ACCESSIBILITY.md), [architecture](docs/ARCHITECTURE.md), and [data notes](docs/DATA.md).

## Known limitations

- The OSM query treats eligible roads as undirected and filters obvious non-walking highways/access restrictions. It does not yet model every directional or conditional pedestrian rule.
- Origins and entities snap to the nearest node, not the nearest point along an edge; the API reports snap distance.
- The translucent boundary is a degree-based display buffer, not a precise polygon isochrone. Reachable street segments are the authoritative result.
- Areas are listed when a reachable segment intersects their polygon. No population estimate is made because the current normalized data does not support one defensibly.
- MapLibre and its demo basemap load from the internet.

## Next three steps (not part of V0.2)

1. Add essential-service POIs such as groceries, pharmacies, and parks.
2. Add GTFS transit and Walk → Ride → Walk journeys.
3. Compare neighborhood-level 15-minute accessibility with defensible population indicators.
