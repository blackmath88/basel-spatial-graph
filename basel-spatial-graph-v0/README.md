# Basel Spatial Graph

Heterogeneous public city data — OpenStreetMap street networks, cantonal open
government datasets, official population statistics and the Swiss GTFS timetable —
joined once into a **typed relational graph of Basel**, queryable through a structured API and
an MCP adapter, where **every answer carries its own provenance**.

> The join is precomputed and correct, and the answer carries its own provenance.

Four sources that normally live in four incompatible shapes — polygons, POI rows, a routing
graph, a timetable — are normalized, snapped and related ahead of time. Traversal, filtering,
grouping and aggregation run over typed relations. Anything that depends on a travel mode, a
budget or a departure time is computed *at request time* by deterministic routing engines, never
stored and never averaged away, and is labelled a live computation rather than a stored fact.

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

Three kinds of statement in one answer, kept apart rather than blended: `children` is **official**
statistics (data.bs.ch `100128`, reference year 2025), `transit stops` is a **derived** structural
relation persisted in the graph, and `pharmacies` / `nearest` are **dynamic** — a real Dijkstra run
for these parameters, which would differ for others. The response says so, per field, with the
dataset and retrieval date behind each. See [docs/SPATIAL_GRAPH.md](docs/SPATIAL_GRAPH.md) and
[docs/QUERY_API.md](docs/QUERY_API.md).

## The reference application — 15-Minute Basel

The map is **one client of the core**, not the product. Click anywhere in Basel, pick a travel mode
and a time budget, and see what everyday life you can actually reach — routed along the real
OpenStreetMap pedestrian and bicycle networks and the official Swiss timetable, never drawn as a
circle.

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

It exercises the same engines the graph calls, which is the point: one deterministic spatial core,
several front doors — a map, an HTTP query API, a CLI and an MCP server.

```text
Reference application — the 15-Minute Basel map
  V0    GIS graph                        ✅
  V0.2  Real walking network             ✅
  V0.3  15-minute services               ✅
  V0.4  Walking + Cycling + Transit      ✅

Spatial Graph Core — the same data, relationally queryable
  P1    Heterogeneous Basel graph        ✅
  P2    Structured query API             ✅ (grouping + aggregation)
  P2.5  Provenance foundation            ✅
  P3    MCP adapter                      ✅ (local stdio)
  P4    Natural-language planning        next / later
```

## Quick start

```bash
git clone <this repo>
cd basel-spatial-graph/basel-spatial-graph-v0

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

uvicorn app.main:app --reload
```

`requirements.txt` is the runtime and test set only. Refreshing the data additionally needs
`pip install -r requirements-prepare.txt` (OSMnx, which pulls in geopandas and pandas) — the
running server never imports it.

Then open <http://127.0.0.1:8000> (API docs at <http://127.0.0.1:8000/docs>).

**No preparation step, no downloads.** The repository ships `data/processed/` as a
**frozen snapshot of real Basel data** — 24.8 MB of prepared artefacts, ~8 MB in the clone —
so every capability below works on the first start: the map, walking, cycling, walk + transit,
the Spatial Graph Core, the standing questions and the MCP tools. The server reads those files
at startup (~1.8 s) and never opens a socket.

The snapshot is **real, and it is not current**. It is a photograph of Basel taken on 2026-08-20,
not a live feed. `python -m app.prepare_data` re-downloads everything and is the only way to move
it forward — see [The frozen snapshot](#the-frozen-snapshot).

Before drawing conclusions from any answer, read **[Known limitations](#known-limitations)**: what
the frozen data can and cannot support, where snapping degrades quality, and what the model does
not contain at all.

The optional agent interface needs Python 3.10+ because FastMCP does. Keep it
isolated from the Python 3.9-compatible reference app:

```bash
python3.10 -m venv .venv-mcp
source .venv-mcp/bin/activate
pip install -r requirements.txt -r requirements-mcp.txt
python -m app.mcp.server
```

See [docs/MCP.md](docs/MCP.md) for tools and client configuration.

### Refreshing the data

You never need this to run the project — only to move the snapshot forward. It needs the
preparation dependencies (`pip install -r requirements-prepare.txt`) and a network connection.
`python -m app.prepare_data` downloads everything again (~4 min) and prints exactly what it got:

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

Snapshot

  manifest: data/processed/SNAPSHOT.json (frozen 2026-08-21T14:04:48+00:00)
  entities         1.7 MB  matches
  walk             8.3 MB  differs
  bike             3.7 MB  differs
  services         1.0 MB  differs
  transit          5.9 MB  matches
  spatial_graph    4.1 MB  differs
  data_quality     0.0 MB  differs
  population       0.0 MB  matches

  Some artefacts differ from the committed snapshot; the server will report them as `local`.
  To publish them as the new frozen snapshot: python -m app.snapshot --write

----------------------------------------------------------
status  streets:  LIVE
status  bike:     LIVE
status  entities: LIVE
status  services: LIVE
status  transit:  LIVE
status  snapshot: local (differs from the committed snapshot)
status  overall:  READY
----------------------------------------------------------
```

It exits `0` when everything is live and `1` when anything fell back to fixture data.

Preparing data does **not** re-freeze the snapshot. Freshly downloaded files would otherwise
relabel themselves as "the frozen snapshot" and the distinction would be worthless, so the
server reports them as `local` until you deliberately re-freeze and commit:

```bash
python -m app.snapshot            # what on disk still matches the committed snapshot?
python -m app.snapshot --write    # re-describe data/processed/ as the new frozen snapshot
git add data/processed && git commit -m "Refresh the Basel snapshot"
```

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

## Where the data comes from

Four independent providers, each keeping its own licence and attribution. Full detail, with dataset
identifiers, URLs and retrieval dates, is in **[ATTRIBUTION.md](../ATTRIBUTION.md)**.

| Provider | Contributes | Licence |
|---|---|---|
| [OpenStreetMap](https://www.openstreetmap.org/) via [OSMnx](https://osmnx.readthedocs.io/) | the pedestrian and bicycle networks; `grocery`, `pharmacy`, `park`, `library` and part of `healthcare` | ODbL 1.0, © OpenStreetMap contributors |
| [Open Government Data Basel-Stadt](https://data.bs.ch/) | neighbourhoods `100042`, schools `100029`, accidents `100120`, sport `100151`, POI `100015`, population `100128` | Open Government Data Basel-Stadt (CC BY 3.0 CH) |
| [opentransportdata.swiss](https://data.opentransportdata.swiss/dataset/timetable-2026-gtfs2020) | the Swiss national timetable (GTFS 2020), feed `20260819` | Open data, opentransportdata.swiss (attribution required) |
| [CARTO](https://carto.com/attributions) + OpenStreetMap | the basemap raster tiles, loaded by the browser only | per provider, attributed in the map |

OSMnx downloads street networks and points of interest from OpenStreetMap as ready-to-use
NetworkX/GeoPandas objects. We ask it for `network_type="walk"` and `network_type="bike"` over
*Basel-Stadt, Switzerland*, which keeps footways, paths, pedestrian zones, steps, residential and
living streets and drops motorways and other car-only ways.

Source data, derived artefacts, the frozen snapshot and request-time computations are four
different kinds of statement, and the project never blends them — see
[ATTRIBUTION.md](../ATTRIBUTION.md#four-different-kinds-of-statement) and
[docs/DATA.md](docs/DATA.md).

**Code licence: [MIT](../LICENSE).** It covers the source only; the committed data under
`data/processed/` stays under the upstream licences above.

## The frozen snapshot

`data/processed/` is committed. That is what makes `git clone && uvicorn app.main:app` work.

| File | Contents | Real? | Needed at startup for | MB |
|---|---|---|---|---:|
| `basel_walking_network.graphml` | OSM pedestrian graph: 14,102 nodes / 19,258 edges / 884 km, `length_m`, geometry, `highway`, `name`, OSM ids | real (OSM) | walking, transit walk legs, all snapping, the map | 8.3 |
| `basel_transit.npz` | The Basel timetable subset: 1,437 stops, 246 routes, 200,696 trips | real (opentransportdata.swiss) | walk + transit | 5.9 |
| `basel_spatial_graph.json` | The heterogeneous typed graph: 4,034 nodes, 14,092 edges | derived | Spatial Graph Core, standing questions, MCP | 4.1 |
| `basel_cycling_network.graphml` | OSM bicycle graph: 5,918 nodes / 584 km | real (OSM) | cycling | 3.7 |
| `basel_entities.json` | Normalized areas / schools / accidents | real (data.bs.ch) | the entity graph, the map, `/analysis/*` | 1.7 |
| `basel_services.json` | 1,308 services, each with a walk **and** a bike access node, snap distance and quality | real (data.bs.ch + OSM) | every accessibility answer, `/services` | 1.0 |
| `basel_population.json` | Neighbourhood population by age group, 10 years, data.bs.ch `100128` | official | *build time only* — rebuilding the graph offline | 0.03 |
| `data_quality.json` | Generated counts, missing names, bad snaps, duplicates, warnings | derived | `/data/status`, provenance caveats | 0.02 |
| `SNAPSHOT.json` | The manifest: size, SHA-256 and generation / retrieval / reference date per artefact | — | telling frozen from local | 0.01 |

24.8 MB on disk, roughly 8 MB in a clone. Everything above is **normalized, prepared output**.

What is *not* committed, and never will be:

| Not committed | Why | Size |
|---|---|---|
| `data/raw/gtfs/gtfs_ch.zip` | The Swiss GTFS archive is an input to extraction, not a runtime dependency | 224 MB |
| `data/raw/osmnx_cache/` | OSMnx's raw Overpass responses; only makes a `--refresh` cheaper | 24 MB |
| `data/raw/*.json` | Raw Basel Open Data responses, kept locally for inspection | 2.8 MB |

No Git LFS, no remote storage, no download at startup: the artefacts are small enough to be
ordinary files in an ordinary repository.

### It is real data, and it is not current

Every figure in the snapshot came from the real source it names. None of it is refreshed by
running the server. `SNAPSHOT.json` carries the dates so nothing has to be guessed:

```bash
python -m app.snapshot        # per-artefact: matches / differs / missing
curl 'http://127.0.0.1:8000/health' | jq .snapshot
```

```json
{
  "state": "frozen",
  "label": "frozen snapshot",
  "is_frozen_snapshot": true,
  "note": "A frozen snapshot of real Basel data, prepared once and committed so the server runs
           straight after `git clone` with no downloads. It is real, and it is not current…",
  "created_at": "2026-08-21T14:04:48+00:00",
  "valid_until": "2026-12-12",
  "refresh_command": "python -m app.prepare_data"
}
```

`valid_until` is the **last service date in the frozen timetable**. Past it, transit answers stop
being meaningful until the snapshot is refreshed; walking, cycling, the graph and the questions are
unaffected. The OSM networks and the POI catalogue drift more slowly and more quietly — a shop that
closed last month is still in the snapshot, and looks exactly like a shop that is open.

## Refreshing, and forcing fixture mode

```bash
python -m app.prepare_data --refresh          # re-download and re-extract everything
python -m app.prepare_data --network-only     # just the two street networks
python -m app.prepare_data --services-only    # just the service POIs
python -m app.prepare_data --entities-only    # just the Basel entity datasets
python -m app.prepare_data --transit-only     # just the timetable
python -m app.prepare_data --population-only  # just the demographic data
python -m app.prepare_spatial_graph           # just the heterogeneous graph (~1.3 s)

python -m app.snapshot                        # does the disk still match the committed snapshot?
python -m app.snapshot --write                # re-freeze data/processed/ as the new snapshot

BASEL_GRAPH_FIXTURE=1 uvicorn app.main:app --reload       # synthetic everything, fully offline
BASEL_SERVICE_SOURCE=fixture uvicorn app.main:app         # synthetic services, real streets
BASEL_TRANSIT_SOURCE=fixture uvicorn app.main:app         # synthetic timetable
BASEL_STREET_NETWORK_SOURCE=osmnx uvicorn app.main:app    # refuse to start without a live network
BASEL_TRANSIT_SOURCE=gtfs uvicorn app.main:app            # refuse to start without a live timetable
```

Tunable defaults: `BASEL_WALKING_SPEED_KMH`, `BASEL_CYCLING_SPEED_KMH`, `BASEL_MAX_TRANSFERS`,
`BASEL_MIN_TRANSFER_SECONDS`, `BASEL_STOP_TRANSFER_RADIUS_M`.

`python -m app.prepare_spatial_graph` rebuilds the heterogeneous graph from the committed
snapshot alone, so it works offline in a fresh clone — that is why `basel_population.json` is
committed even though the server never reads it.

Service snapping is stored with a fingerprint of the network it was made against. Re-prepare the
network alone and the next start re-snaps in memory rather than trusting stale node ids —
`/health` reports it as `services.resnapped_at_startup`.

Preparing data leaves `data/processed/` dirty in `git status`. That is deliberate: replacing the
snapshot the repository ships is a reviewable commit, not a side effect of running a script.
`git checkout -- data/processed` puts the frozen snapshot back.

## How to tell frozen from local from fixture

Three data states, never conflated:

| State | Meaning |
|---|---|
| `frozen` | Byte-identical to the committed snapshot — you are running exactly what the repository ships. Real Basel data, not current. |
| `local` | You ran `python -m app.prepare_data`; this artefact is newer than the committed snapshot. |
| `fixture` | The subsystem fell back to synthetic data. Deterministic and offline, but **not Basel** — no figure derived from it describes the real city. |

The state is resolved at startup by hashing each artefact against `SNAPSHOT.json` (~20 ms for
all of them) and surfaced everywhere an answer can be read:

- five header badges — `streets / bike / transit / services` and the snapshot itself, blue when
  frozen, green when locally prepared, orange when fixture;
- the *Data sources & quality* panel, with per-category counts and every warning;
- `GET /health` — a `snapshot` block plus a `data_state` on every subsystem;
- `GET /data/status` — the same `snapshot` block alongside the quality report;
- `GET /spatial-graph/status` — `data_state` for the graph;
- `provenance.data_state` in every structured query answer, standing question and MCP result,
  next to `provenance.mode` and the per-dataset `retrieved_at`.

A subsystem's own verdict always wins: a server that fell back to the fixture reports `fixture`
whatever is lying on disk. When a source is unavailable the app still starts, says why
(`fallback_reason`), and never labels synthetic data as real.

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

386 tests, all deterministic and fully offline — the suite blocks socket connections outright and
routes over tiny hand-built graphs and a four-stop synthetic timetable, so it never depends on
OpenStreetMap, data.bs.ch or opentransportdata.swiss being reachable, and never on the committed
snapshot either.

With the quick-start install alone, 383 pass and 3 skip explicitly: two OpenStreetMap POI-source
tests need `geopandas` (`requirements-prepare.txt`) and one MCP test needs `fastmcp`
(`requirements-mcp.txt`). Install those and all 386 run.

## Known limitations

Read this section before drawing any conclusion about Basel from an answer. The quality figures
below are those of the **committed snapshot**; `/data/status` reports whatever is actually loaded.

### Data currency

- **The committed data are frozen, not live.** Every artefact was retrieved on **2026-08-20**
  (per-artefact `prepared_at` in `data/processed/SNAPSHOT.json`; the manifest itself was written
  the following day). Nothing refreshes them but `python -m app.prepare_data`, and re-freezing the
  snapshot is a separate, deliberate `python -m app.snapshot --write`.
- **The shipped timetable expires.** Its last service date is **2026-12-12**, published as
  `valid_until` in the manifest and in `/health`. Past that date transit answers stop describing
  scheduled service. Walking, cycling, the entity graph and the population figures do **not**
  become invalid on that date — but they do go quietly stale: a shop that closed after
  2026-08-20 is still in the snapshot and looks exactly like a shop that is open.
- **Population is annual, not current.** Reference years 2016–2025; the latest is a year-end
  cantonal figure, not a live headcount.

### Model and scope
- **The spatial graph works at neighbourhood scale.** Every accessibility figure
  stands for a whole Wohnviertel from one representative origin, and nothing is
  population-weighted because the population data is not finer than that.
- **The query language is intentionally smaller than SQL.** It supports typed
  grouping, aggregation, HAVING and ordering, but not arbitrary joins, OR
  expressions or derived-field formulas.
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
- **No demand data anywhere.** No ridership, origin–destination flows, footfall, capacity or
  opening hours. Every figure is supply-side: what exists and what can be reached, never how many
  people actually go there or how busy it is when they do.
- **One city, one snapshot.** The model is Basel-Stadt at one moment. There is no second city to
  compare against and no time series of accessibility — only the population dimension has years.
- **Snapping is the main derived-quality risk.** Origins, services and stops attach to the nearest
  network node, not the nearest point along an edge. In the frozen snapshot that leaves 1,154 of
  1,437 timetable stops unattachable to the pedestrian network, 19 services outside it entirely,
  and 33 services snapped further than 150 m. Every snap distance and quality grade is reported
  per record, and the snapshot's report raises 18 warnings in total — `/data/status` has them all.
- **Duplicates are reported, not removed** — in the snapshot, 34 school pairs and 14 healthcare
  pairs within 25 m; two pharmacies really can share a building. See `/data/status`.
- **65 of 138 parks have no name.** The UI shows "Park (unnamed)"; the stored `name` stays `null`.
- **Pedestrian rules are approximated.** Every retained way is walkable in both directions; no slope,
  stairs penalty, surface, barriers or construction.
- **Origins and services snap to the nearest node**, not the nearest point along an edge; every snap
  distance and quality grade is reported.
- **The basemap and MapLibre load from the internet** (CARTO/OSM raster tiles), so the map needs a
  connection even though the API does not. Every API endpoint, the CLI, the MCP server and the
  whole test suite are fully offline.
- **Refreshing needs the network, the preparation dependencies and about four minutes**, including
  a 224 MB GTFS download. There is no incremental update: `prepare_data` re-fetches whichever
  subsystem you ask for in full.
- **The MCP adapter is local stdio only.** No remote transport, no authentication, no hosted
  endpoint; a client launches it as a subprocess. Geometry and route itineraries are not exposed
  through it. See [docs/MCP.md](docs/MCP.md).

## Documentation

**Reference application** — [Concept](docs/CONCEPT.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Accessibility model](docs/ACCESSIBILITY.md) · [Services](docs/SERVICES.md) ·
[Cycling](docs/CYCLING.md) · [Transit](docs/TRANSIT.md) · [Data & provenance](docs/DATA.md)

**Spatial Graph Core** — [Concept](docs/SPATIAL_GRAPH_MCP_CONCEPT.md) ·
[The graph](docs/SPATIAL_GRAPH.md) · [Query API](docs/QUERY_API.md) · [MCP](docs/MCP.md) ·
[City2Graph evaluation](docs/CITY2GRAPH.md)

**Repository** — [Attribution and data licences](../ATTRIBUTION.md) · [Code licence (MIT)](../LICENSE)

`docs/CONCEPT.md`, `docs/SPATIAL_GRAPH_MCP_CONCEPT.md` and `docs/CITY2GRAPH.md` are exploratory
design documents kept for the reasoning they record — including options that were evaluated and
rejected. They are marked as such and are not setup instructions.
