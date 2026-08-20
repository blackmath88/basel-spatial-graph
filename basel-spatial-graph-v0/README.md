# 15-Minute Basel

Click anywhere in Basel, pick 5, 10 or 15 minutes, and see **what everyday life you can actually
reach on foot** — routed along the real OpenStreetMap pedestrian network, not drawn as a circle.

```text
15 minutes from Barfüsserplatz

Schools       44    nearest 2.1 min — Schulhaus Mücke
Groceries     25    nearest 0.2 min — Coop
Pharmacies    18    nearest 4.2 min — cityapotheke
Healthcare    42    nearest 1.1 min — Ultraschallpraxis Freie Strasse
Parks         24    nearest 3.2 min — Park (unnamed)
Sport         23    nearest 3.5 min — Gymnasium am Münsterplatz

6 / 6 essential categories reachable
```

```text
V0    GIS graph              ✅
V0.2  Real walking network   ✅
V0.3  15-minute services     ✅
V0.4  Multimodal transit     next
V1    Spatial Graph API      later
```

## Quick start

```bash
git clone <this repo>
cd basel-spatial-graph/basel-spatial-graph-v0

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

python -m app.prepare_data          # downloads and caches everything (~35 s, once)

uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000> (API docs at <http://127.0.0.1:8000/docs>).

`python -m app.prepare_data` prints exactly what it got:

```text
Preparing Basel Spatial Graph...

Preparing Basel walking network...

  source:  OpenStreetMap / OSMnx
  place:   Basel-Stadt, Switzerland
  nodes:   14,102
  edges:   19,258
  length:  884.1 km of walkable ways
  CRS:     EPSG:4326 (distances computed in EPSG:2056)
  cached:  data/processed/basel_walking_network.graphml (written)

Preparing Basel entities (areas, schools, accidents)...

  source:  data.bs.ch (Open Government Data Basel-Stadt)
  areas      21
  schools    415
  accidents  1,500 (capped at 1,500)
  cached:  data/processed/basel_entities.json (written)

Services

  school        415  via bs       Open Government Data Basel-Stadt (data.bs.ch)
  sport         310  via bs       Open Government Data Basel-Stadt (data.bs.ch)
  culture        72  via bs       Open Government Data Basel-Stadt (data.bs.ch)
  healthcare    111  via bs+osm   Open Government Data Basel-Stadt (data.bs.ch), OpenStreetMap
  grocery       166  via osm      OpenStreetMap
  pharmacy       63  via osm      OpenStreetMap
  park          138  via osm      OpenStreetMap
  library        33  via osm      OpenStreetMap

Snapping services to walking network...
  done. 1,308 attached · 33 poor snaps · 19 not attached

  total:   1,308 service locations
  cached:  data/processed/basel_services.json (written)

Data-quality report: data/processed/data_quality.json (13 warning(s))

----------------------------------------------------------
status  streets:  LIVE
status  entities: LIVE
status  services: LIVE
status  overall:  READY
----------------------------------------------------------
```

It exits `0` when everything is live and `1` when anything fell back to fixture data.

## What you can do

| In the app | What happens |
|---|---|
| Click the map | Routes the pedestrian network and builds a 15-minute profile for that spot |
| 5 / 10 / 15 min | Recomputes network, service counts, nearest times and completeness |
| Tick a category | Shows or hides those POIs; reachable ones are bright, the rest stay faint |
| Click a service | Its walking time, network distance, snap quality and full provenance — plus the shortest path drawn on the map |
| Click a category row | Jumps to that category's nearest service and routes to it |
| Straight-line radius | Overlays the dashed Euclidean circle, so *nearby* and *reachable* can be compared |

## Service categories

| Category | Essential | Source | Prepared |
|---|:--:|---|---:|
| `school` | ✅ | data.bs.ch `100029` | 415 |
| `sport` | ✅ | data.bs.ch `100151` (Sportamt BS) | 310 |
| `grocery` | ✅ | OpenStreetMap | 166 |
| `park` | ✅ | OpenStreetMap | 138 |
| `healthcare` | ✅ | data.bs.ch `100015` + OpenStreetMap | 111 |
| `pharmacy` | ✅ | OpenStreetMap | 63 |
| `culture` | — | data.bs.ch `100015` | 72 |
| `library` | — | OpenStreetMap | 33 |

Official Basel-Stadt data wherever the canton publishes it, OpenStreetMap for the rest. A category
may merge several providers — healthcare combines the canton's clinics with OSM doctors' practices.
Every location keeps its own source, dataset, source id, licence and retrieval timestamp.
Details in [docs/SERVICES.md](docs/SERVICES.md).

## The 15-minute completeness indicator

```text
✓ Grocery   ✓ Pharmacy   ✓ Healthcare   ✓ School   ✓ Park   ✗ Sport

5 / 6 essential categories reachable
```

A category counts when **at least one** of its locations is reachable within the budget. That is the
whole rule, and the app shows it on demand.

It is labelled **"Prototype accessibility completeness"** everywhere. It is not an official urban
quality score: no weighting by population, opening hours, capacity, size or quality. One kiosk counts
the same as a supermarket.

## What is OSMnx and where does the data come from?

[OSMnx](https://osmnx.readthedocs.io/) downloads street networks and points of interest from
[OpenStreetMap](https://www.openstreetmap.org/) as ready-to-use NetworkX/GeoPandas objects. We ask it
for `network_type="walk"` over *Basel-Stadt, Switzerland*, which keeps footways, paths, pedestrian
zones, steps, residential and living streets and drops motorways and other car-only ways. OSM is
community-maintained and published under ODbL; attribution travels in every API response.

Basel entities and several service categories come from the official
[Open Government Data Basel-Stadt](https://data.bs.ch/) portal.

## What gets cached

| File | Contents | Written by |
|---|---|---|
| `data/processed/basel_walking_network.graphml` | Walking graph: nodes with lon/lat, edges with `length_m`, geometry, `highway`, `name`, OSM ids | `prepare_data` |
| `data/processed/basel_services.json` | 1,308 normalized services **with their access node, snap distance and quality** | `prepare_data` |
| `data/processed/basel_entities.json` | Normalized areas / schools / accidents | `prepare_data` |
| `data/processed/data_quality.json` | Generated counts, missing names, bad snaps, duplicates, warnings | `prepare_data` |
| `data/raw/osmnx_cache/` | OSMnx's raw Overpass responses, so a `--refresh` is cheap | OSMnx |
| `data/raw/*.json` | Raw Basel Open Data responses, for inspection | `prepare_data` |

**The server never downloads anything.** `uvicorn` reads these caches at startup (~1.3 s) and then
answers queries from memory. Restarting or `--reload` does not re-download or re-snap anything.

## Refreshing, and forcing fixture mode

```bash
python -m app.prepare_data --refresh          # re-download everything
python -m app.prepare_data --network-only     # just the walking network
python -m app.prepare_data --services-only    # just the service POIs
python -m app.prepare_data --entities-only    # just the Basel entity datasets

BASEL_GRAPH_FIXTURE=1 uvicorn app.main:app --reload       # synthetic everything, fully offline
BASEL_SERVICE_SOURCE=fixture uvicorn app.main:app         # synthetic services, real streets
BASEL_STREET_NETWORK_SOURCE=osmnx uvicorn app.main:app    # refuse to start without a live network
```

Service snapping is stored with a fingerprint of the network it was made against. Re-prepare the
network alone and the next start re-snaps in memory rather than trusting stale node ids —
`/health` reports it as `services.resnapped_at_startup`.

## How to tell LIVE from FIXTURE

- three header badges — green `streets / services / entities: live`, orange when fixture;
- the *Data sources & quality* panel, with per-category counts and every warning;
- `GET /health`, `GET /data/status`, and `provenance.mode` in every accessibility response.

When a source is unavailable the app still starts, says why (`fallback_reason`), and never labels
synthetic data as real.

## How walking time becomes distance

```text
distance budget (m) = walking speed (km/h) × 1000 × minutes / 60
```

At the default **4.8 km/h**: 5 min → 400 m, 10 min → 800 m, 15 min → 1,200 m. That budget is spent
along graph edges by Dijkstra, so a river or a railway cutting costs real metres. A service is
reachable when `cost(origin → its access node) + its snap distance ≤ budget`. Change the speed per
request with `walking_speed_kmh`, or globally with `BASEL_WALKING_SPEED_KMH`.

## API

```bash
# the full answer: network geometry + services + completeness
curl 'http://127.0.0.1:8000/accessibility/walk?lat=47.5556&lon=7.5906&minutes=15'

# just the profile — counts, nearest times, completeness; no geometry
curl 'http://127.0.0.1:8000/accessibility/walk/services?lat=47.5556&lon=7.5906&minutes=15'

# only some categories
curl 'http://127.0.0.1:8000/accessibility/walk?lat=47.5556&lon=7.5906&minutes=10&categories=grocery,pharmacy'

# the shortest walking path to one service
curl 'http://127.0.0.1:8000/accessibility/walk/route?lat=47.5556&lon=7.5906&service_id=service:pharmacy:osm:node:3888944673'

# the catalogue
curl 'http://127.0.0.1:8000/services'
curl 'http://127.0.0.1:8000/services/pharmacy'
curl 'http://127.0.0.1:8000/services/geojson?categories=grocery,park'

# invert the query: where is this category NOT reachable?
curl 'http://127.0.0.1:8000/analysis/accessibility-gaps?category=pharmacy&minutes=10'

# data health
curl 'http://127.0.0.1:8000/health'
curl 'http://127.0.0.1:8000/data/status'
```

Response (abbreviated):

```json
{
  "origin": {"lat": 47.5556, "lon": 7.5906},
  "snapped_origin": {"node_id": "205496022", "snap_distance_m": 6.2, "component_size": 14102},
  "minutes": 15,
  "walking_speed_kmh": 4.8,
  "network": {
    "reachable_node_count": 1754,
    "reachable_edge_count": 2362,
    "reachable_edge_length_m": 76296.3,
    "distance_budget_m": 1200.0
  },
  "reachable_services": {
    "grocery": {
      "label": "Groceries", "essential": true, "count": 25,
      "nearest_minutes": 0.2, "nearest_name": "Coop", "prepared_total": 166,
      "items": [{
        "id": "service:grocery:osm:node:4437700046",
        "category": "grocery", "name": "Coop",
        "geometry": {"type": "Point", "coordinates": [7.5907408, 47.5556543]},
        "walking_distance_m": 16.6, "walking_time_minutes": 0.2,
        "access": {"node_id": "205496022", "snap_distance_m": 16.6, "quality": "good"},
        "provenance": {"source": "OpenStreetMap", "source_id": "node/4437700046",
                       "license": "ODbL 1.0", "retrieved_at": "2026-08-20T09:25:33+00:00"}
      }]
    }
  },
  "completeness": {
    "label": "Prototype accessibility completeness",
    "reachable_categories": ["grocery", "pharmacy", "healthcare", "school", "park", "sport"],
    "missing_categories": [], "reachable_count": 6, "total": 6
  },
  "geometry": {"type": "FeatureCollection", "features": ["…"]},
  "provenance": {"network_source": "OpenStreetMap / OSMnx", "mode": "live",
                 "services_mode": "live", "algorithm": "NetworkX single-source Dijkstra"}
}
```

Every geometry feature carries a `kind`: `reachable_edge` (the authoritative answer),
`straight_line_radius` (Euclidean, for comparison only) and, with `include_buffer=true`,
`network_buffer` (an explicitly approximate polygon).

Errors are JSON, not stack traces:

```json
{"error": "outside_network",
 "message": "No walkable street within 1000 m of this location (nearest node is 208276 m away). …",
 "details": {"snap_distance_m": 208276.5, "max_snap_distance_m": 1000.0}}
```

## Reachable vs. nearby

Tick **straight-line radius** to overlay the dashed Euclidean circle of the same budget. The gap
between the circle and the blue network is the whole point: from Barfüsserplatz a 15-minute circle
covers 4.5 km² of map, while the walking network reaches 76 km of street in a much more ragged shape,
cut short by the Rhine, the rail corridor and the motorway. Each category's nearest service reports a
`network_detour_factor` for the same reason.

## Tests

```bash
pytest
```

190 tests, all deterministic and fully offline — the suite blocks socket connections outright and
routes over tiny hand-built graphs, so it never depends on OpenStreetMap or data.bs.ch being reachable.

## Known limitations

- **POI completeness is the weak link.** OSM covers central Basel groceries and pharmacies well;
  doctors' practices are patchy. A missing POI looks exactly like a genuine accessibility gap.
- **Coverage is the canton of Basel-Stadt** (city + Riehen + Bettingen). Clicking in Germany or
  France returns `outside_network`. 19 catalogue entries — regional museums in Weil am Rhein,
  Saint-Louis and Baselland — are outside the network and flagged `unreachable` rather than snapped.
- **Completeness counts categories, not quality.** One kiosk equals a supermarket; opening hours,
  capacity and price are not modelled.
- **Gap analysis measures street coverage, not people.** Coverage is computed at walking-network
  nodes; residential density is not taken into account. The methodology ships in the response.
- **Duplicates are reported, not removed** — 34 school pairs share an address, and two pharmacies
  really can share a building. See `/data/status`.
- **65 of 138 parks have no name.** The UI shows "Park (unnamed)"; the stored `name` stays `null`.
- **Pedestrian rules are approximated.** Every retained way is walkable in both directions; no slope,
  stairs penalty, surface, barriers or construction.
- **Origins and services snap to the nearest node**, not the nearest point along an edge; every snap
  distance and quality grade is reported.
- **The basemap and MapLibre load from the internet** (CARTO/OSM raster tiles), so the map needs a
  connection even though the API does not.

## Documentation

[Concept](docs/CONCEPT.md) · [Architecture](docs/ARCHITECTURE.md) · [Services](docs/SERVICES.md) ·
[Data & provenance](docs/DATA.md) · [Accessibility model](docs/ACCESSIBILITY.md)
