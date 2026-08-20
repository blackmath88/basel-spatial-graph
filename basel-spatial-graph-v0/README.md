# 15-Minute Basel

Click anywhere in Basel, pick a travel mode and a time budget, and see **what everyday life you can
actually reach** — routed along the real OpenStreetMap pedestrian and bicycle networks and the
official Swiss timetable, never drawn as a circle.

```text
15 minutes from Barfüsserplatz

                  WALKING     CYCLING     WALK + TRANSIT

Groceries              25         154                 69
Pharmacies             18          60                 36
Healthcare             42          91                 58
Schools                44         386                108
Parks                  24         117                 48
Sport                  23         218                 62

categories reachable  6/6         6/6                6/6
```

```text
Reference application — the 15-Minute Basel map
  V0    GIS graph                        ✅
  V0.2  Real walking network             ✅
  V0.3  15-minute services               ✅
  V0.4  Walking + Cycling + Transit      ✅

Spatial Graph Core — the same data, relationally queryable
  P1    Heterogeneous Basel graph        ✅
  P2    Structured query API             ✅ (started here)
  P3    MCP adapter                      later
  P4    Natural-language planning        later
```

The map is now one client among several. There is a second way in: a typed
heterogeneous graph of 4,034 Basel entities that answers cross-domain questions
without rendering anything.

```bash
python -m app.spatial_graph.cli ask q6_children_underserved --table
```

```text
Which neighbourhoods with many children have below-median access to both
pharmacies and public transport?

  name          children   child_share   pharmacies   nearest   transit stops
  St. Alban         2237         17.5%            1    12.0 min             5
  Bruderholz        1994         20.5%            1     4.7 min            11
  Hirzbrunnen       1914         19.1%            2     8.7 min            14
```

Official population statistics, structural graph relations and a live routing
computation, in one answer that states which part came from where. See
[docs/SPATIAL_GRAPH.md](docs/SPATIAL_GRAPH.md).

## Quick start

```bash
git clone <this repo>
cd basel-spatial-graph/basel-spatial-graph-v0

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

python -m app.prepare_data          # downloads and caches everything (~4 min, once)
                                    # …and builds the spatial graph at the end

uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000> (API docs at <http://127.0.0.1:8000/docs>).

`python -m app.prepare_data` prints exactly what it got:

```text
Preparing Basel Spatial Graph...

Walking network

  source:  OpenStreetMap / OSMnx
  place:   Basel-Stadt, Switzerland
  nodes:   14,102
  edges:   19,258
  length:  884.1 km of walkable ways
  cached:  data/processed/basel_walking_network.graphml (written)

Cycling network

  source:  OpenStreetMap / OSMnx
  nodes:   5,918
  edges:   8,034
  length:  584.0 km of cyclable ways
  cached:  data/processed/basel_cycling_network.graphml (written)

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

Service → walk network attachments
  valid: 1,289   poor snaps: 33   not attached: 19
Service → bike network attachments
  valid: 1,289   poor snaps: 34   not attached: 19

  total:   1,308 service locations
  cached:  data/processed/basel_services.json (written)

Transit

  ... 1,437 stations inside the extraction box
  ... 33,182,263 stop_times rows scanned, 200,696 local trips kept

Stop → walking network attachments
  valid: 283   poor snaps: 43   outside the walking network: 1,154
  source:  opentransportdata.swiss
  feed:    Swiss national timetable (GTFS 2020) (20260819)
  stops:   1,437
  routes:  246
  trips:   200,696
  service dates: 20251214 – 20261212 (covers today)
  cached:  data/processed/basel_transit.npz (written)

Data-quality report: data/processed/data_quality.json (18 warning(s))

----------------------------------------------------------
status  streets:  LIVE
status  bike:     LIVE
status  entities: LIVE
status  services: LIVE
status  transit:  LIVE
status  overall:  READY
----------------------------------------------------------
```

It exits `0` when everything is live and `1` when anything fell back to fixture data.

## What you can do

| In the app | What happens |
|---|---|
| **Walking / Cycling / Walk + Transit** | Switches the whole answer: network, counts, nearest times, completeness |
| Click the map | Routes from that spot and builds the profile |
| 5 / 10 / 15 / 30 min | Recomputes everything |
| **Departure time** (transit only) | Re-runs against the timetable — miss the tram and the answer changes |
| Tick a category | Shows or hides those POIs; reachable ones are bright, the rest stay faint |
| Click a service | Its travel time, snap quality and provenance — plus the route drawn on the map, and for transit the full **walk → board → wait → ride → exit → walk** itinerary |
| Click a category row | Jumps to that category's nearest service and routes to it |
| **Compare all three modes** | The table above, for your origin and budget |
| Straight-line radius | Overlays the dashed Euclidean circle, so *nearby* and *reachable* can be compared |

## The three modes

| Mode | Network | Cost model | Default |
|---|---|---|---|
| **Walking** | OSM pedestrian, 14,102 nodes / 884 km | network distance ÷ speed | 4.8 km/h |
| **Cycling** | OSM bicycle, 5,918 nodes / 584 km — a genuinely different graph | network distance ÷ speed | 15 km/h |
| **Walk + Transit** | pedestrian network + the Swiss timetable | walk + **wait** + ride + transfer + walk | 4.8 km/h walking, ≤ 1 transfer |

Waiting is never assumed away, and a transit answer depends on when you leave.
Details: [cycling](docs/CYCLING.md) · [transit](docs/TRANSIT.md).

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
| `data/processed/basel_cycling_network.graphml` | The bicycle-accessible graph, same shape | `prepare_data` |
| `data/processed/basel_transit.npz` | The Basel timetable subset: 1,437 stops, 246 routes, 200,696 trips (6 MB) | `prepare_data` |
| `data/raw/gtfs/gtfs_ch.zip` | The 224 MB Swiss GTFS archive, kept so a re-extract need not re-download | `prepare_data` |
| `data/processed/basel_services.json` | 1,308 normalized services **with a walk and a bike access node, snap distance and quality each** | `prepare_data` |
| `data/processed/basel_entities.json` | Normalized areas / schools / accidents | `prepare_data` |
| `data/processed/basel_population.json` | Neighbourhood population by age group, 10 years, from data.bs.ch `100128` | `prepare_data` |
| `data/processed/basel_spatial_graph.json` | The heterogeneous typed graph: 4,034 nodes, 14,092 edges (4.3 MB) | `prepare_spatial_graph` |
| `data/processed/data_quality.json` | Generated counts, missing names, bad snaps, duplicates, warnings | `prepare_data` |
| `data/raw/osmnx_cache/` | OSMnx's raw Overpass responses, so a `--refresh` is cheap | OSMnx |
| `data/raw/*.json` | Raw Basel Open Data responses, for inspection | `prepare_data` |

**The server never downloads anything.** `uvicorn` reads these caches at startup (~1.8 s) and then
answers queries from memory. Restarting or `--reload` does not re-download the OSM graphs, re-extract
the 2.9 GB of GTFS, or re-snap anything.

## Refreshing, and forcing fixture mode

```bash
python -m app.prepare_data --refresh          # re-download and re-extract everything
python -m app.prepare_data --network-only     # just the two street networks
python -m app.prepare_data --services-only    # just the service POIs
python -m app.prepare_data --entities-only    # just the Basel entity datasets
python -m app.prepare_data --transit-only     # just the timetable
python -m app.prepare_data --population-only # just the demographic data
python -m app.prepare_spatial_graph          # just the heterogeneous graph (~1.3 s)

BASEL_GRAPH_FIXTURE=1 uvicorn app.main:app --reload       # synthetic everything, fully offline
BASEL_SERVICE_SOURCE=fixture uvicorn app.main:app         # synthetic services, real streets
BASEL_TRANSIT_SOURCE=fixture uvicorn app.main:app         # synthetic timetable
BASEL_STREET_NETWORK_SOURCE=osmnx uvicorn app.main:app    # refuse to start without a live network
BASEL_TRANSIT_SOURCE=gtfs uvicorn app.main:app            # refuse to start without a live timetable
```

Tunable defaults: `BASEL_WALKING_SPEED_KMH`, `BASEL_CYCLING_SPEED_KMH`, `BASEL_MAX_TRANSFERS`,
`BASEL_MIN_TRANSFER_SECONDS`, `BASEL_STOP_TRANSFER_RADIUS_M`.

Service snapping is stored with a fingerprint of the network it was made against. Re-prepare the
network alone and the next start re-snaps in memory rather than trusting stale node ids —
`/health` reports it as `services.resnapped_at_startup`.

## How to tell LIVE from FIXTURE

- four header badges — green `streets / bike / transit / services: live`, orange when fixture;
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
# one endpoint, three modes
curl 'http://127.0.0.1:8000/accessibility?lat=47.5556&lon=7.5906&mode=walk&minutes=15'
curl 'http://127.0.0.1:8000/accessibility?lat=47.5556&lon=7.5906&mode=bike&minutes=15'
curl 'http://127.0.0.1:8000/accessibility?lat=47.5556&lon=7.5906&mode=transit&minutes=15&departure_time=14:30'

# the comparison table
curl 'http://127.0.0.1:8000/accessibility/compare?lat=47.5556&lon=7.5906&minutes=15&departure_time=14:30'

# one readable itinerary: walk → board → wait → ride → exit → walk
curl 'http://127.0.0.1:8000/accessibility/transit/route?lat=47.5556&lon=7.5906&service_id=...&departure_time=14:30'

# the timetable itself
curl 'http://127.0.0.1:8000/transit/status'
curl 'http://127.0.0.1:8000/transit/routes'
curl 'http://127.0.0.1:8000/transit/stops?q=Barf'

# mode-specific endpoints (the V0.3 walking one is unchanged)
curl 'http://127.0.0.1:8000/accessibility/walk?lat=47.5556&lon=7.5906&minutes=15'
curl 'http://127.0.0.1:8000/accessibility/bike?lat=47.5556&lon=7.5906&minutes=15&cycling_speed_kmh=18'

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

### Spatial Graph Core

```bash
# discover the graph rather than being told about it
curl 'http://127.0.0.1:8000/spatial-graph/schema'
curl 'http://127.0.0.1:8000/spatial-graph/entity-types'

# a bounded relational query: demographics + structure + live routing
curl -X POST 'http://127.0.0.1:8000/spatial-graph/query' \
     -H 'Content-Type: application/json' \
     -d @examples/queries/children_pharmacy_transit.json

# the standing cross-domain questions
curl 'http://127.0.0.1:8000/spatial-graph/questions'
curl 'http://127.0.0.1:8000/spatial-graph/questions/q1_poorest_access?category=pharmacy'

# where any number came from
curl 'http://127.0.0.1:8000/spatial-graph/provenance/LOCATED_IN'
```

Or without the server at all:

```bash
python -m app.spatial_graph.cli describe
python -m app.spatial_graph.cli ask q4_category_inequality --table
python -m app.spatial_graph.cli query examples/queries/elderly_healthcare_gap.json
python -m app.spatial_graph.cli export experiments/export   # CSV + Cypher
```

Grammar and operators: [docs/QUERY_API.md](docs/QUERY_API.md).

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
      "ids": ["service:grocery:osm:node:4437700046", "…"],
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
  "provenance": {"travel_mode": "walk", "network_kind": "walk",
                 "network_source": "OpenStreetMap / OSMnx", "mode": "live",
                 "routing_method": "network distance / 4.8 km/h",
                 "services_mode": "live", "algorithm": "NetworkX single-source Dijkstra"}
}
```

A transit answer adds the parts that only transit has:

```json
{
  "mode": "transit",
  "departure_time": "2026-08-20T14:30+02:00",
  "service_date": "2026-08-20",
  "service_date_is_requested_date": true,
  "max_transfers": 1,
  "transit": {
    "stops_in_walking_range": 28, "stops_reached": 93, "stops_reached_by_vehicle": 80,
    "routes_used": [{"label": "Tram 11", "vehicle": "Tram", "agency": "Basler Verkehrsbetriebe"}]
  },
  "reachable_services": {"pharmacy": {"items": [{
    "display_name": "Wettstein Apotheke", "travel_time_minutes": 9.1,
    "journey": {
      "uses_transit": true, "total_minutes": 9.1,
      "walking_minutes": 5.0, "waiting_minutes": 1.2, "transit_minutes": 3.0, "transfers": 0,
      "routes": ["Tram 2"],
      "boarding_stop": {"name": "Basel, Bankverein"},
      "exit_stop": {"name": "Basel, Wettsteinplatz"},
      "steps": [
        {"kind": "walk",  "minutes": 3.8, "detail": "to the stop"},
        {"kind": "board", "stop": "Basel, Bankverein", "route": "Tram 2",
         "headsign": "Riehen, Grenze", "departure": "14:35:00"},
        {"kind": "wait",  "minutes": 1.2},
        {"kind": "ride",  "route": "Tram 2", "minutes": 3.0, "stops": 2},
        {"kind": "exit",  "stop": "Basel, Wettsteinplatz", "arrival": "14:38:00"},
        {"kind": "walk",  "minutes": 1.1, "detail": "to the destination"}
      ]
    }
  }]}},
  "provenance": {
    "travel_mode": "transit",
    "routing_method": "walk + wait + ride + transfer + walk",
    "algorithm": "walk Dijkstra + RAPTOR (round-based transit search) + walk Dijkstra",
    "timezone": "Europe/Zurich", "max_transfers": 1, "walking_speed_kmh": 4.8,
    "transit": {"source": "opentransportdata.swiss", "feed_version": "20260819",
                "retrieved_at": "2026-08-20T…"}
  }
}
```

Every category row carries **`ids`** (every reachable service, for map highlighting) and **`items`**
(the detailed rows, capped at 60 per category — raise it with `service_limit`).

Every geometry feature carries a `kind`: `reachable_edge` (the authoritative answer),
`straight_line_radius` (Euclidean, for comparison only), `network_buffer` (with
`include_buffer=true`, explicitly approximate) and, in transit mode, `transit_segment` and
`transit_stop` for the rides actually taken and the stops they reached. A route response adds
`walk_leg`, `transit_leg`, `transfer_leg` and `walk_leg_final`.

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

342 tests, all deterministic and fully offline — the suite blocks socket connections outright and
routes over tiny hand-built graphs and a four-stop synthetic timetable, so it never depends on
OpenStreetMap, data.bs.ch or opentransportdata.swiss being reachable.

## Known limitations

- **The spatial graph works at neighbourhood scale.** Every accessibility figure
  stands for a whole Wohnviertel from one representative origin, and nothing is
  population-weighted because the population data is not finer than that.
- **The query language has no GROUP BY** — aggregating across rows still needs
  code. It is the clearest known gap.
- **Cycling is a prototype cost model**: `distance ÷ 15 km/h`. No slope, traffic stress, cycle-lane
  preference, surface, turn penalties or one-way rules — Bruderholz is a real climb and the model
  does not know. See [docs/CYCLING.md](docs/CYCLING.md).
- **Transit is static GTFS**: no realtime, no delays, no disruptions. Ride segments are drawn as
  straight lines between stops (the times are exact, the drawn line is schematic).
- **One transfer by default** (`max_transfers`, up to 3). No bike + transit, no fares, no step-free
  routing.
- **You can ride past the walking network but not board there.** The timetable box reaches Lörrach,
  Liestal and Saint-Louis; the pedestrian network is canton-only, so 283 of 1,437 stations can be
  boarded from. Every prepared service is inside the canton, so nothing reachable is lost.
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

**Reference application** — [Concept](docs/CONCEPT.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Accessibility model](docs/ACCESSIBILITY.md) · [Services](docs/SERVICES.md) ·
[Cycling](docs/CYCLING.md) · [Transit](docs/TRANSIT.md) · [Data & provenance](docs/DATA.md)

**Spatial Graph Core** — [Concept](docs/SPATIAL_GRAPH_MCP_CONCEPT.md) ·
[The graph](docs/SPATIAL_GRAPH.md) · [Query API](docs/QUERY_API.md) ·
[City2Graph evaluation](docs/CITY2GRAPH.md)
