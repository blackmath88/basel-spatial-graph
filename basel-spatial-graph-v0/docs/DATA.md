# Data and provenance

## Walking network — LIVE

| | |
|---|---|
| Source | OpenStreetMap, via [OSMnx](https://osmnx.readthedocs.io/) `graph_from_place("Basel, Switzerland", network_type="walk")` |
| Licence | ODbL 1.0, © OpenStreetMap contributors |
| Size | 10,232 nodes · 13,877 undirected edges · ~620 km of walkable ways |
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

Coverage is the city of Basel only. Riehen, Bettingen and the cross-border suburbs are outside it, and a
click there returns a clear `outside_network` error rather than a snap across the city.

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

## Fixtures and failures

`app/fixtures.py` holds synthetic entities. `street_sources/fixture_source.py` holds a synthetic 7×5 grid
with a deliberate barrier crossed on only two rows — it makes "nearby but not reachable" testable. Both
exist for deterministic tests and offline demos only.

Fallback is always explicit and always reported:

```bash
python -m app.prepare_data          # exits 1 and prints FIXTURE if anything fell back
curl localhost:8000/health          # entities.mode / streets.mode + fallback_reason
```

Every accessibility response carries `provenance.mode` and `provenance.network.fixture`. The header badges
turn orange. Nothing synthetic is ever labelled live.

To force fixture mode: `BASEL_GRAPH_FIXTURE=1` (everything) or `BASEL_STREET_NETWORK_SOURCE=fixture`
(streets only). To make the app *refuse* to run on fixture streets, set `BASEL_STREET_NETWORK_SOURCE=osmnx`.

## Derived data

- `IN_AREA`, `ADJACENT_TO`, `NEAR` and `ACCESS_POINT` are derived structural relations.
- Walking routes, times, area intersections and detour factors are analytical results, computed per request.
- No population accessibility value is returned: intersecting a street network with a neighbourhood
  population total would imply precision the data does not support.
