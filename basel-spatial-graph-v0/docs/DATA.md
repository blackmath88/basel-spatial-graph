# Data and provenance

Source licences and per-dataset attribution: [../../ATTRIBUTION.md](../../ATTRIBUTION.md).

## What the repository ships — the frozen snapshot

Everything described below is committed to the repository under `data/processed/`, so the server
runs immediately after `git clone` with no downloads. The committed artefacts are **real data,
frozen at one moment** — the manifest `data/processed/SNAPSHOT.json` records each artefact's size,
SHA-256 and whatever generation, retrieval or reference date it carries, and `valid_until` records
the last service date in the frozen timetable.

Each section below is headed *real, frozen in the snapshot*: the source is genuine, and the copy
committed here was retrieved on **2026-08-20** and does not change until it is refreshed. The
application's own word for "real source rather than fixture" is `live` — that is what `mode: "live"`
and the `LIVE` banner in `prepare_data` mean, and it says nothing about how recent the data is.
The `data_state` below is what says that.

At startup every artefact is hashed against that manifest, and each subsystem reports one of three
data states: `frozen` (identical to the committed snapshot), `local` (prepared since by
`python -m app.prepare_data`) or `fixture` (synthetic fallback — not Basel). The state travels in
`/health`, `/data/status`, `/spatial-graph/status` and `provenance.data_state`. The raw download
caches — the 224 MB GTFS archive, the OSMnx Overpass cache, the raw API responses — are **not**
committed; they are inputs to preparation, never runtime dependencies.

`python -m app.snapshot` compares disk with the manifest; `--write` re-freezes it. Preparing data
never re-freezes on its own, so freshly downloaded files cannot quietly relabel themselves as the
shipped snapshot.

## Walking network — real, frozen in the snapshot

| | |
|---|---|
| Source | OpenStreetMap, via [OSMnx](https://osmnx.readthedocs.io/) `graph_from_place("Basel-Stadt, Switzerland", network_type="walk")` |
| Licence | ODbL 1.0, © OpenStreetMap contributors |
| Size | 14,102 nodes · 19,258 undirected edges · ~884 km of walkable ways |
| Cache | `data/processed/basel_walking_network.graphml` |
| Refresh | `python -m app.prepare_data --refresh` |

`network_type="walk"` keeps footways, paths, pedestrian areas, steps, living and residential streets and
service roads, and drops motorways, trunk roads and anything tagged `foot=no`. It treats every retained
way as walkable in both directions, which is right for pedestrians and wrong for cars — that is the point.

The cache stores, per edge: `length_m` (metres, from OSMnx's geodesic length), the WGS84 geometry as WKT,
`highway`, `name` and the OSM way id. Per node: `lon`, `lat` and the OSM node id. Graph-level attributes
carry the full provenance record — source, licence, place query, retrieval timestamp, OSMnx version.

OSMnx's own HTTP cache lives in `data/raw/osmnx_cache/`, so `--refresh` re-parses without re-downloading
unless the upstream response changed.

Coverage is the **canton** of Basel-Stadt: the city plus Riehen and Bettingen. That is deliberate — the
Basel-Stadt service datasets cover the whole canton, and a city-only network left 80 of them
unroutable. The German and French suburbs are still outside it, and a click there returns a clear
`outside_network` error rather than a snap across the border.

## Entity sources — real, frozen in the snapshot

| Entity | Basel-Stadt dataset | Records | Role |
|---|---:|---:|---|
| Area | `100042` | 21 | Wohnviertel polygons |
| School | `100029` | 415 | School locations |
| Accident | `100120` | 1,500 of ~11,900 | Geocoded road accidents, newest first |

From the Basel-Stadt Opendatasoft Explore API 2.1. That API caps `limit` at 100, so ingestion pages
through it with `offset`; asking for 500 records in one call returns HTTP 400. Accidents are capped at
`BASEL_ACCIDENT_LIMIT` (default 1,500) and ordered `vu_jahr desc`, so a truncated slice is still a
meaningful one.

Normalized records keep the dataset id, the upstream record id, the source URL and the licence. Schools
have no stable upstream key, so their id is derived from their coordinates — stable across re-ingestion,
but it would change if a school moved in the source data.

Cached at `data/processed/basel_entities.json`; raw responses are kept in `data/raw/*.json` for inspection.

## Cycling network — real, frozen in the snapshot

| | |
|---|---|
| Source | OpenStreetMap via OSMnx, `network_type="bike"`, same place query |
| Size | 5,918 nodes · 8,034 undirected edges · ~584 km of cyclable ways |
| Cache | `data/processed/basel_cycling_network.graphml` |

A genuinely separate graph: the bicycle filter drops footways, steps and `bicycle=no` ways, so it
has ~300 km *less* road than the pedestrian network. See [the cycling guide](CYCLING.md).

## Public transport — real, frozen in the snapshot

| | |
|---|---|
| Source | [opentransportdata.swiss](https://data.opentransportdata.swiss/dataset/timetable-2026-gtfs2020), official Swiss national GTFS |
| Licence | Open data, attribution required |
| Archive | 224 MB zip (`stop_times.txt` alone is 2.9 GB uncompressed) |
| Extraction | lat 47.42–47.68, lon 7.40–7.90 — Basel plus Baselland, Lörrach, Weil am Rhein, Saint-Louis |
| Size | 1,437 stations · 246 routes · 200,696 trips · 1,997 patterns |
| Service window | 2025-12-14 – 2026-12-12 |
| Cache | `data/processed/basel_transit.npz` (6 MB) |

Extracted in one streaming pass, never loaded whole. Platforms are collapsed into their parent
station. 283 of the 1,437 stations sit inside the pedestrian network and can therefore be boarded
from or alighted at; the rest can be ridden through. See [the transit guide](TRANSIT.md).

## Service locations — real, frozen in the snapshot

1,308 everyday destinations across eight categories, from official Basel-Stadt datasets where they
exist and OpenStreetMap where they do not:

| Category | Source | Count |
|---|---|---:|
| School | data.bs.ch `100029` | 415 |
| Sport | data.bs.ch `100151` | 310 |
| Grocery | OpenStreetMap | 166 |
| Park | OpenStreetMap | 138 |
| Healthcare | data.bs.ch `100015` + OpenStreetMap | 111 |
| Culture | data.bs.ch `100015` | 72 |
| Pharmacy | OpenStreetMap | 63 |
| Library | OpenStreetMap | 33 |

Cached at `data/processed/basel_services.json` — including each location's access node, snap
distance and snap quality **for every prepared network** (walking and cycling separately), plus a
fingerprint per network so a re-prepared graph invalidates only its own snapping.
See [the services guide](SERVICES.md) for the tag mappings, snapping rules and completeness limits.

## The data-quality report

`python -m app.prepare_data` also writes `data/processed/data_quality.json` and exposes a short form
at `/data/status`. It records, per category: counts, which sources contributed, how many locations
have no upstream name, how many snapped poorly or not at all, snap-distance median/p95/max, and
possible duplicate pairs within 25 m. The report committed with the frozen snapshot
(generated 2026-08-20) raises **18 warnings** — among them 19 services outside the walking network
(regional museums in Germany, France and Baselland listed by the Basel Info dataset), 34 possible
duplicate school pairs within 25 m, and 1,154 of 1,437 timetable stops that could not be attached
to the pedestrian network. `/data/status` always reports the loaded report rather than this
number, so a refreshed snapshot corrects it automatically.

## Neighbourhood population — real, frozen in the snapshot

| | |
|---|---|
| Source | data.bs.ch `100128` — *Wohnbevölkerung nach Geschlecht, Alter, Staatsangehörigkeit und Wohnviertel* |
| Licence | Open Government Data Basel-Stadt (CC BY 3.0 CH) |
| Unit | Wohnviertel (all 21, including Riehen and Bettingen) |
| Years | 2016–2025 prepared of 49 available (1974–2025); latest reference year 2025 |
| Size | 210 observations · canton total 210,529 in 2025 |
| Cache | `data/processed/basel_population.json` |

Resident population by *single year of age*, aggregated server-side into six documented groups:
`total`, `children` (0–17), `young` (0–19), `working_age` (20–64), `elderly` (65+),
`elderly_80_plus`. `young` and `elderly` match the cantonal Jugend-/Altersquotient definitions on
purpose. Nothing is estimated or interpolated. See [the spatial graph guide](SPATIAL_GRAPH.md).

## Fixtures and failures

`app/fixtures.py` holds synthetic entities. `street_sources/fixture_source.py` holds a synthetic 7×5 grid
with a deliberate barrier crossed on only two rows — it makes "nearby but not reachable" testable.
`service_sources/fixture_source.py` holds twelve synthetic services placed against that grid, covering
every essential category (and one deliberately unnamed park). The bicycle fixture grid covers the same
positions but crosses the barrier and uses its own node ids, so nothing can silently route cycling over
the pedestrian graph. `transit_sources/fixture_source.py` holds a four-stop timetable with weekday,
weekend and after-midnight services and one calendar exception. `app/population.py` holds a
hand-written population table, and `spatial_graph/fixtures.py` assembles all of them into a fully
synthetic heterogeneous graph. All of them exist for deterministic tests and offline demos only.

Fallback is always explicit and always reported:

```bash
python -m app.prepare_data          # exits 1 and prints FIXTURE if anything fell back
curl localhost:8000/health          # entities.mode / streets.mode + fallback_reason
```

Every accessibility response carries `provenance.mode` and `provenance.network.fixture`. The header badges
turn orange. Nothing synthetic is ever labelled live.

To force fixture mode: `BASEL_GRAPH_FIXTURE=1` (everything), `BASEL_STREET_NETWORK_SOURCE=fixture`
(streets only), `BASEL_SERVICE_SOURCE=fixture` (services only) or `BASEL_TRANSIT_SOURCE=fixture`
(timetable only). To make the app *refuse* to run on fixture data, set
`BASEL_STREET_NETWORK_SOURCE=osmnx` or `BASEL_TRANSIT_SOURCE=gtfs`.

## Derived data

- `IN_AREA`, `ADJACENT_TO`, `NEAR` and `ACCESS_POINT` (for both entities and services) are derived
  structural relations, computed once and cached.
- Walking routes, times, reachable-service lists, area intersections and detour factors are analytical
  results, computed per request.
- The `5 / 6` completeness figure is a derived **prototype indicator**, labelled as such in the API and
  the UI, with its definition shipped in the response.
- Accessibility-gap coverage is an **exploratory** analytical result measured at network nodes, not at
  residents; the response carries its own methodology statement.
- No population accessibility value is returned: intersecting a street network with a neighbourhood
  population total would imply precision the data does not support.
