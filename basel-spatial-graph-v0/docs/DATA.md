# Data and provenance

## Entity sources

| Entity | Basel-Stadt dataset | Role |
|---|---:|---|
| Area | `100042` | Statistical neighborhood polygons |
| School | `100029` | School locations |
| Accident | `100120` | Geocoded road accidents |

These adapters use the Basel-Stadt Opendatasoft Explore API 2.1. Normalized records retain dataset and source identifiers.

## Walking network

The source adapter in `app/street.py` requests highway ways from OpenStreetMap through Overpass. It excludes motorways, trunks, unfinished ways, and explicit private/no-access ways. Source attribution, retrieval time, ODbL license, and EPSG:4326 CRS are returned in API provenance.

The first successful response is cached as `data/processed/basel_walk_network.json`; delete that one file to refresh it on the next start. Set `BASEL_STREET_NETWORK_SOURCE=fixture` to bypass cache/network loading.

OpenStreetMap is community-maintained rather than an official Basel-Stadt dataset. Its topology and access tagging can contain gaps. V0.2 does not silently call its fixture real data.

## Fixtures and failures

`app/fixtures.py` contains synthetic entities. `fixture_street_network()` contains a synthetic grid with a deliberate barrier and limited crossings. They exist for stable tests and offline demonstration only. `/health` reports entity and street modes plus fallback reasons.

If live loading is slow or unavailable, run:

```bash
BASEL_GRAPH_FIXTURE=1 uvicorn app.main:app --reload
```

## Derived data

- `IN_AREA`, `ADJACENT_TO`, `NEAR`, and `ACCESS_POINT` are derived structural relations.
- Walking routes, times, area intersections, and comparison factors are analytical results generated per request.
- No population accessibility value is returned: intersecting a street network with a neighborhood population total would imply unsupported precision.
