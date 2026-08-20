# Services — the everyday destinations

V0.3 turns the walking-network demonstrator into an accessibility service. The question is no
longer "how far can I walk?" but **"what parts of everyday life can I reach on foot from here?"**

## The eight categories

| Category id | Label | Essential? | Source | What it contains |
|---|---|:--:|---|---|
| `grocery` | Groceries | ✅ | OpenStreetMap | `shop=supermarket \| convenience \| greengrocer \| grocery` |
| `pharmacy` | Pharmacies | ✅ | OpenStreetMap | `amenity=pharmacy` |
| `healthcare` | Healthcare | ✅ | data.bs.ch `100015` **+** OpenStreetMap | Cantonal clinics and hospitals, plus OSM doctors' practices |
| `school` | Schools | ✅ | data.bs.ch `100029` | Official Basel-Stadt school locations |
| `park` | Parks | ✅ | OpenStreetMap | `leisure=park`, `landuse=village_green \| recreation_ground`, ≥ 500 m², public |
| `sport` | Sport | ✅ | data.bs.ch `100151` | Sport- und Bewegungsanlagen (Sportamt BS) |
| `library` | Libraries | — | OpenStreetMap | `amenity=library` |
| `culture` | Culture | — | data.bs.ch `100015` | Basel Info POIs in *Kultur & Unterhaltung* |

The first six are the **essential** categories the completeness indicator is built from. `library`
and `culture` are prepared and mappable but never affect the score.

Category ids are a Python enum (`app/service_model.py`), not free-form strings. Display labels live
in a separate table, so they can be renamed or translated without touching any data or API contract.

## One model, many providers

Every location — whether it came from an Opendatasoft REST API or an Overpass query — becomes the
same `ServiceLocation`:

```python
ServiceLocation(
    id="service:grocery:osm:node:4437700046",
    category=ServiceCategory.GROCERY,
    name="Coop",                      # None when upstream has no name — never invented
    lon=7.5907408, lat=47.5556543,
    source="OpenStreetMap",
    source_dataset="OSM shop=supermarket|convenience|greengrocer|grocery",
    source_id="node/4437700046",
    source_url="https://www.openstreetmap.org/node/4437700046",
    license="ODbL 1.0",
    retrieved_at="2026-08-20T09:25:33+00:00",
    attributes={"shop": "supermarket", "brand": "Coop"},
    access_node_id="205496022", access_distance_m=16.6, access_quality="good",
)
```

Which provider serves which category is one readable table in
[`app/service_sources/__init__.py`](../app/service_sources/__init__.py):

```python
SOURCE_PLAN = {
    ServiceCategory.SCHOOL:     ("bs",),
    ServiceCategory.HEALTHCARE: ("bs", "osm"),   # merged: clinics + practices
    ServiceCategory.GROCERY:    ("osm",),
    …
}
```

A category can list several providers; a provider that fails is recorded per category and the others
still run. No category is tied to a single supplier, and nothing downstream — the accessibility
service, the API, the map — knows or cares which provider a location came from.

## How services attach to the walking network

```text
ServiceLocation ──ACCESS_POINT──> StreetNode ──WALKABLE_TO──> StreetNode …
```

Snapping happens **once**, during `python -m app.prepare_data`, and is stored in the service cache
together with a fingerprint of the network it was snapped against. The server reads it; if the
network has since been re-prepared, the fingerprint no longer matches and snapping is redone in
memory rather than pointing at node ids that no longer exist (`/health` reports
`services.resnapped_at_startup`).

Two details matter:

- **Point services** snap from their position.
- **Area services** (parks, sports grounds) snap from the nearest point of their *outline*. A large
  park's centre can be 200 m from any street while its gate is on the pavement, so the outline is
  stored — simplified to 5 m — and every vertex is a snapping candidate.

Each snap is graded and the grade is carried through to the UI:

| Quality | Distance | Routable? |
|---|---|---|
| `good` | ≤ 150 m | yes |
| `poor` | 150–500 m | yes, but flagged everywhere it appears |
| `unreachable` | > 500 m | no — kept in the catalogue, excluded from routing |

Reachability itself is **never** materialized. There are no `REACHABLE` edges between origins and
services; a query recomputes it from the Dijkstra cost map, which is one dictionary lookup per
reachable node.

## What "reachable" means

A service is reachable within a time budget when

```text
cost(origin → its access node)  +  its snap distance   ≤   budget
```

where `cost` is the shortest path **through the pedestrian network**, weighted by real edge lengths
in metres, and `budget = speed_kmh × 1000 × minutes / 60` (400 / 800 / 1,200 m at the default
4.8 km/h).

That is deliberately different from proximity. A supermarket 300 m away across the Rhine with no
bridge in between is *near* and not *reachable*. The response reports both: every category's nearest
service comes with a `network_detour_factor` in `euclidean_vs_network`, and the map can overlay the
dashed straight-line circle of the same budget for comparison.

## The 15-minute completeness indicator

```text
✓ Grocery   ✓ Pharmacy   ✓ Healthcare   ✓ School   ✓ Park   ✗ Sport

5 / 6 essential categories reachable
```

A category counts when **at least one** prepared location of that category is reachable within the
budget. That is the whole definition, and the UI shows it on demand.

It is labelled **"Prototype accessibility completeness"** everywhere it appears. It is *not* an
official urban-quality score. It does not weight by population, opening hours, capacity, quality,
size, price or whether the shop is open on a Sunday. One kiosk counts the same as a supermarket.
Treat it as a legible summary of the data, not as a verdict on a neighbourhood.

## Inverting the query: accessibility gaps

```bash
curl 'http://127.0.0.1:8000/analysis/accessibility-gaps?category=pharmacy&minutes=10'
```

Because the walking graph is undirected, the distance from a node to the nearest pharmacy equals the
distance from *all* pharmacies to that node. One multi-source Dijkstra — seeded at every pharmacy's
access node, offset by its snap distance — answers it for all 14,102 nodes at once, in well under a
second.

The response reports the covered share of the network, a per-neighbourhood breakdown (worst first)
and a spatially thinned sample of the worst uncovered points. At 10 minutes it puts Bettingen at 0 %
pharmacy coverage and Riehen at 43 % — which is what you would expect.

**Method and its limits are in the response itself** (`method` field): coverage is measured at
walking-network nodes, not at residents. It is a proxy for walkable *street* coverage. Nobody lives
on most of these nodes in equal numbers, and residential density is not taken into account, so this
is an exploratory tool for finding places to look at — not a population-weighted accessibility
statistic.

## Known limits of POI completeness

- **OpenStreetMap is community-maintained.** Groceries and pharmacies are well covered in central
  Basel; doctors' practices are patchy, and a missing POI looks exactly like a genuine gap.
- **Basel-Stadt datasets cover the canton**, and the Basel Info POI dataset reaches beyond it — the
  Vitra Design Museum (Weil am Rhein), Fondation Fernet-Branca (Saint-Louis) and a few Baselland
  venues are in the catalogue but outside the walking network, so they are kept and flagged
  `unreachable` rather than dragged onto a distant Swiss street.
- **Duplicates are reported, never removed.** 34 school pairs sit within 25 m of each other because
  the dataset lists several school types at one address; two pharmacies really can share a building.
  See `duplicate_candidates` in the data-quality report.
- **65 of 138 parks have no name.** The UI shows "Park (unnamed)"; the stored `name` stays `null`.
- **Nothing is time-aware.** Opening hours, term times and closures are not modelled.

## Refreshing

```bash
python -m app.prepare_data --services-only            # reuse the cache if valid
python -m app.prepare_data --services-only --refresh  # re-download every provider
```

The report at `data/processed/data_quality.json` (and `/data/status`) is regenerated each run.
