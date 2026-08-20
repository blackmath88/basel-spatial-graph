# 15-Minute Basel Spatial Graph

Click anywhere in Basel, pick 5, 10 or 15 minutes, and see what you can **actually reach on foot** — routed along the real OpenStreetMap pedestrian network, not drawn as a circle.

```text
V0    GIS graph              ✅
V0.2  Real walking network   ✅
V0.3  15-Minute services     next
```

## Quick start

```bash
git clone <this repo>
cd basel-spatial-graph/basel-spatial-graph-v0

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

python -m app.prepare_data          # downloads and caches the data (~20 s, once)

uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000> (API docs at <http://127.0.0.1:8000/docs>).

`python -m app.prepare_data` prints exactly what it got:

```text
Preparing Basel walking network...

  source:  OpenStreetMap / OSMnx
  place:   Basel, Switzerland
  nodes:   10,232
  edges:   13,877
  length:  619.5 km of walkable ways
  CRS:     EPSG:4326 (distances computed in EPSG:2056)
  cached:  data/processed/basel_walking_network.graphml (written)

Preparing Basel entities (areas, schools, accidents)...

  source:  data.bs.ch (Open Government Data Basel-Stadt)
  areas      21
  schools    415
  accidents  1,500 (capped at 1,500)
  cached:  data/processed/basel_entities.json (written)

----------------------------------------------------------
status  streets:  LIVE
status  entities: LIVE
----------------------------------------------------------
```

It exits `0` when everything is live and `1` when anything fell back to fixture data.

## What is OSMnx and where does the data come from?

[OSMnx](https://osmnx.readthedocs.io/) is a Python library that downloads street networks from
[OpenStreetMap](https://www.openstreetmap.org/) and returns them as ready-to-route NetworkX graphs.
We ask it for `network_type="walk"` over the place *Basel, Switzerland*, which keeps footways, paths,
pedestrian zones, steps, residential and living streets, and drops motorways and other car-only ways.
OpenStreetMap is community-maintained, published under ODbL; attribution is carried in every API response.

Basel entities (neighbourhoods, schools, accidents) come from the official
[Open Government Data Basel-Stadt](https://data.bs.ch/) portal, datasets `100042`, `100029` and `100120`.

## What gets cached

| File | Contents | Written by |
|---|---|---|
| `data/processed/basel_walking_network.graphml` | Normalized walking graph: nodes with lon/lat, edges with `length_m`, geometry, `highway`, `name` and OSM ids | `prepare_data` |
| `data/processed/basel_entities.json` | Normalized areas / schools / accidents | `prepare_data` |
| `data/raw/osmnx_cache/` | OSMnx's raw Overpass responses, so a `--refresh` is cheap | OSMnx |
| `data/raw/*.json` | Raw Basel Open Data responses, for inspection | `prepare_data` |

**The server never downloads anything.** `uvicorn` reads these caches at startup (~0.9 s) and then answers
queries from memory. Restarting or `--reload` does not re-download the network.

## Refreshing, and forcing fixture mode

```bash
python -m app.prepare_data --refresh        # re-download everything
python -m app.prepare_data --network-only   # just the walking network
python -m app.prepare_data --entities-only  # just the Basel datasets

BASEL_GRAPH_FIXTURE=1 uvicorn app.main:app --reload      # synthetic data, fully offline
BASEL_STREET_NETWORK_SOURCE=fixture uvicorn app.main:app # synthetic streets, real entities
BASEL_STREET_NETWORK_SOURCE=osmnx uvicorn app.main:app   # refuse to start without a live cache
```

## How to tell LIVE from FIXTURE

Three places, all saying the same thing:

- the two badges in the header — green `streets: live` / `entities: live`, orange when fixture;
- the sidebar's `Data · streets` and `Data · entities` rows after a click;
- `GET /health`, and `provenance.mode` in every accessibility response.

When a source is unavailable the app still starts, but says why (`fallback_reason`) and never labels
synthetic data as real.

## How walking time becomes distance

```text
distance budget (m) = walking speed (km/h) × 1000 × minutes / 60
```

At the default **4.8 km/h**: 5 min → 400 m, 10 min → 800 m, 15 min → 1,200 m. That budget is spent along
graph edges by Dijkstra, so a river or a railway cutting costs you real metres. Change the speed per
request with `walking_speed_kmh`, or globally with `BASEL_WALKING_SPEED_KMH`.

## API

```bash
curl 'http://127.0.0.1:8000/accessibility/walk?lat=47.5556&lon=7.5906&minutes=15'
curl 'http://127.0.0.1:8000/accessibility/walk?lat=47.5476&lon=7.5893&minutes=10&walking_speed_kmh=3.5'
curl 'http://127.0.0.1:8000/accessibility/walk?lat=47.5556&lon=7.5906&minutes=15&include_buffer=true'
curl 'http://127.0.0.1:8000/entities/schools/school%3Ab6478f8f0f/accessibility?minutes=10'
curl 'http://127.0.0.1:8000/health'
```

Response (abbreviated):

```json
{
  "origin": {"lat": 47.5556, "lon": 7.5906},
  "snapped_origin": {"node_id": "205496022", "lat": 47.555623, "lon": 7.590525,
                     "snap_distance_m": 6.2, "component_size": 10232},
  "minutes": 15,
  "walking_speed_kmh": 4.8,
  "network": {
    "reachable_node_count": 1754,
    "reachable_edge_count": 2362,
    "reachable_edge_length_m": 76296.3,
    "distance_budget_m": 1200.0,
    "max_network_distance_m": 1199.7
  },
  "geometry": {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[7.590525, 47.555623], "…"]},
     "properties": {"kind": "reachable_edge", "length_m": 41.5, "highway": "pedestrian", "name": "Freie Strasse"}},
    {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": ["…"]},
     "properties": {"kind": "straight_line_radius", "radius_m": 1200.0}}
  ]},
  "reachable_entities": {"schools": ["…"], "school_count": 17, "accident_count": 234, "areas": ["…"]},
  "provenance": {"network_source": "OpenStreetMap / OSMnx", "mode": "live",
                 "algorithm": "NetworkX single-source Dijkstra", "distance_crs": "EPSG:2056"}
}
```

Query parameters: `lat`, `lon`, `minutes` (0–60), `walking_speed_kmh` (0–12),
`include_straight_line` (default `true`), `include_buffer` (default `false`).

Every geometry feature carries a `kind`:

| `kind` | Meaning |
|---|---|
| `reachable_edge` | A street or path segment genuinely reachable within the budget — **the authoritative answer** |
| `straight_line_radius` | The naive Euclidean circle, for comparison only |
| `network_buffer` | Optional 30 m polygon around the reachable network, a visual aid (`include_buffer=true`) |

Errors are JSON, not stack traces:

```json
{"error": "outside_network",
 "message": "No walkable street within 1000 m of this location (nearest node is 208276 m away). …",
 "details": {"snap_distance_m": 208276.5, "max_snap_distance_m": 1000.0}}
```

## Reachable vs. nearby

Tick **straight-line radius** in the sidebar to overlay the dashed Euclidean circle of the same budget.
The gap between the circle and the blue network is the whole point of the project: from Barfüsserplatz a
15-minute circle covers 4.5 km² of map, while the walking network reaches 76 km of street across a much
more ragged shape, cut short by the Rhine, the rail corridor and the motorway.

## Tests

```bash
pytest
```

79 tests, all deterministic and fully offline — the suite blocks socket connections outright and routes
over a tiny hand-built graph, so it never depends on OpenStreetMap being reachable.

## Known limitations

- **Coverage is the city of Basel** (OSM place *Basel, Switzerland*). Riehen, Bettingen and the German
  and French sides are outside the graph; clicking there returns `outside_network`.
- **Pedestrian rules are approximated.** `network_type="walk"` treats every retained way as walkable in
  both directions. Steps, slope, surface, barriers, opening hours and construction are not modelled.
- **Origins snap to the nearest node**, not the nearest point along an edge; the snap distance is reported.
- **Accidents are capped at 1,500** (newest first) to keep startup and the map responsive
  (`BASEL_ACCIDENT_LIMIT` changes it). The full dataset has ~11,900 records.
- **Parallel OSM edges collapse** to the shortest connection per node pair, so a divided carriageway may
  render as one line.
- **Schools have no stable upstream id**, so ids are derived from their coordinates and would change if a
  school moved in the source data.
- **The polygon is never the answer.** The buffered polygon is opt-in and explicitly approximate; only the
  edge features state reachability.
- **The basemap and MapLibre load from the internet** (CARTO/OSM raster tiles), so the map needs a
  connection even though the API does not.

## Documentation

[Concept](docs/CONCEPT.md) · [Architecture](docs/ARCHITECTURE.md) · [Data & provenance](docs/DATA.md) · [Accessibility model](docs/ACCESSIBILITY.md)
