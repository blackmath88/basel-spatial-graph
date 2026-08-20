# Data and provenance

## Walking network — LIVE

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

## Entity sources — LIVE

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

## Service locations — LIVE

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

Cached at `data/processed/basel_services.json` — including each location's walking-network access
node, snap distance and snap quality, plus a fingerprint of the network that produced them.
See [the services guide](SERVICES.md) for the tag mappings, snapping rules and completeness limits.

## The data-quality report

`python -m app.prepare_data` also writes `data/processed/data_quality.json` and exposes a short form
at `/data/status`. It records, per category: counts, which sources contributed, how many locations
have no upstream name, how many snapped poorly or not at all, snap-distance median/p95/max, and
possible duplicate pairs within 25 m. The current run raises 13 warnings — among them 19 services
outside the walking network (regional museums in Germany, France and Baselland listed by the Basel
Info dataset) and 34 school pairs at the same address.

## Fixtures and failures

`app/fixtures.py` holds synthetic entities. `street_sources/fixture_source.py` holds a synthetic 7×5 grid
with a deliberate barrier crossed on only two rows — it makes "nearby but not reachable" testable.
`service_sources/fixture_source.py` holds twelve synthetic services placed against that grid, covering
every essential category (and one deliberately unnamed park). All three exist for deterministic tests
and offline demos only.

Fallback is always explicit and always reported:

```bash
python -m app.prepare_data          # exits 1 and prints FIXTURE if anything fell back
curl localhost:8000/health          # entities.mode / streets.mode + fallback_reason
```

Every accessibility response carries `provenance.mode` and `provenance.network.fixture`. The header badges
turn orange. Nothing synthetic is ever labelled live.

To force fixture mode: `BASEL_GRAPH_FIXTURE=1` (everything), `BASEL_STREET_NETWORK_SOURCE=fixture`
(streets only) or `BASEL_SERVICE_SOURCE=fixture` (services only). To make the app *refuse* to run on
fixture streets, set `BASEL_STREET_NETWORK_SOURCE=osmnx`.

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
