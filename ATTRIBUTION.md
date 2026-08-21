# Attribution

This repository ships a **frozen snapshot of real data** under
`basel-spatial-graph-v0/data/processed/`. That data is not ours. Every source
below materially contributes to the committed snapshot and keeps its own
licence and attribution requirements, which the MIT licence on the source code
does not affect.

Every licence string, dataset identifier and timestamp on this page is taken
from metadata already carried in the repository — the snapshot manifest
`data/processed/SNAPSHOT.json`, the per-record `provenance` blocks, and the
source adapters in `app/`. Nothing here is a legal interpretation.

## Four different kinds of statement

The project keeps these apart everywhere, and so does this page:

| Kind | What it is | Where it lives |
|---|---|---|
| **Source data** | Observations and official statistics published by the providers below | upstream; retrieved by `python -m app.prepare_data` |
| **Derived artefacts** | Normalized networks, snapped service catalogue, the typed graph — computed once from source data | `data/processed/*.graphml`, `*.json`, `*.npz` |
| **Frozen snapshot** | Those derived artefacts, committed at one moment and described by a manifest | `data/processed/SNAPSHOT.json` |
| **Request-time computation** | Isochrones, reachable counts, nearest times, itineraries — computed per request, never stored | API / MCP responses, classified `dynamic` in `provenance` |

A derived artefact inherits the licence of the source it was derived from. A
request-time computation is a statement about the snapshot, not about today.

## OpenStreetMap

| | |
|---|---|
| Contributes | The pedestrian and bicycle street networks, and the `grocery`, `pharmacy`, `park`, `library` service locations plus the OpenStreetMap half of `healthcare` |
| Retrieved via | [OSMnx](https://osmnx.readthedocs.io/) (`graph_from_place`, `features_from_place`) |
| Source URL | <https://www.openstreetmap.org/> |
| Licence | `ODbL 1.0` |
| Attribution | © OpenStreetMap contributors |
| In the snapshot | `basel_walking_network.graphml` (14,102 nodes · 19,258 edges · ~884 km), `basel_cycling_network.graphml` (5,918 nodes · ~584 km), part of `basel_services.json` |
| Retrieved at | 2026-08-20 (per-artefact `retrieved_at` in the manifest) |

The GraphML caches are a **derived** representation: OSMnx's simplified graph,
collapsed to shortest undirected edges, with `length_m`, WGS84 geometry,
`highway`, `name` and the OSM ids retained per edge. They are not a copy of the
OSM database, and they are not current OSM.

## Open Government Data Basel-Stadt (data.bs.ch)

| | |
|---|---|
| Contributes | Neighbourhood polygons, schools, road accidents, resident population by age, and the `school`, `sport`, `culture` service locations plus the cantonal half of `healthcare` |
| Source URL | <https://data.bs.ch/> |
| Licence | `Open Government Data Basel-Stadt (CC BY 3.0 CH)` |

| Dataset | Title | Feeds |
|---|---|---|
| [`100042`](https://data.bs.ch/explore/dataset/100042/) | Wohnviertel | 21 `Neighborhood` nodes, `basel_entities.json` |
| [`100029`](https://data.bs.ch/explore/dataset/100029/) | Schulstandorte | 415 schools, service category `school` |
| [`100120`](https://data.bs.ch/explore/dataset/100120/) | Strassenverkehrsunfälle | 1,500 most recent accidents, `basel_entities.json` |
| [`100151`](https://data.bs.ch/explore/dataset/100151/) | Sport- und Bewegungsanlagen | 310 service locations, category `sport` |
| [`100015`](https://data.bs.ch/explore/dataset/100015/) | Basel Info: Interessante Orte (POI) | categories `culture` (72) and part of `healthcare` |
| [`100128`](https://data.bs.ch/explore/dataset/100128/) | Wohnbevölkerung nach Geschlecht, Alter, Staatsangehörigkeit und Wohnviertel | 210 `PopulationObservation` nodes, reference years 2016–2025 |

The accident slice is capped and ordered newest-first; the population figures
are server-side aggregations of single-year-of-age counts into six documented
groups, with nothing estimated or interpolated.

## opentransportdata.swiss

| | |
|---|---|
| Contributes | The public-transport timetable behind every walk + transit answer |
| Feed | Swiss national timetable (GTFS 2020), feed version `20260819` |
| Dataset URL | <https://data.opentransportdata.swiss/dataset/timetable-2026-gtfs2020> |
| Licence | `Open data, opentransportdata.swiss (attribution required)` |
| In the snapshot | `basel_transit.npz` — 1,437 stops · 246 routes · 200,696 trips |
| Extraction | stops within lat 47.42–47.68, lon 7.4–7.9 (Basel and its cross-border surroundings) |
| Retrieved at | 2026-08-20 |
| **Service dates** | **20251214 – 20261212** |

The committed timetable is a filtered subset of the national feed, re-encoded as
numeric arrays. The 224 MB source archive is **not** committed. The last service
date is published as `valid_until` in the snapshot manifest: after 2026-12-12 the
frozen timetable no longer describes scheduled service, and transit answers stop
being meaningful until the snapshot is refreshed.

## Basemap tiles

The map client loads raster tiles at runtime and attributes them in the map
itself: © OpenStreetMap contributors, © [CARTO](https://carto.com/attributions).
Tiles are not part of the snapshot and are the only thing in the project that
needs an internet connection.

## Software

[OSMnx](https://osmnx.readthedocs.io/), NetworkX, shapely, pyproj, numpy,
FastAPI, uvicorn, pydantic, httpx, pytest and (optionally) FastMCP, each under
its own licence. See `requirements.txt`, `requirements-prepare.txt` and
`requirements-mcp.txt`.

## Fixtures

The synthetic fixtures used by the test suite and offline demos carry the
licence string `fixture-only; not real observations`. They describe a 7×5 grid
and a four-stop timetable, not Basel, and the application reports them as
`fixture` wherever they are in use.

## How attribution travels at runtime

Attribution is not only on this page. Each service location, network, timetable
and population record carries its own `source`, `dataset`, `source_url`,
`license` and `retrieved_at`, and those travel in:

```bash
curl 'http://127.0.0.1:8000/health'                          # per-subsystem source + data_state
curl 'http://127.0.0.1:8000/data/status'                     # snapshot block + quality report
curl 'http://127.0.0.1:8000/spatial-graph/provenance/LOCATED_IN'
curl 'http://127.0.0.1:8000/services/pharmacy'               # per-location provenance
```

Structured query answers, standing questions and MCP results carry a
`provenance.sources` registry keyed by field, plus `provenance.data_state`
saying whether the answer came from the frozen snapshot, locally prepared data,
or fixtures.
