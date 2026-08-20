# Basel Spatial Graph

**Make GIS relational.** A runnable V0 showing how public Basel GIS layers can become a typed graph, queried through an API and inspected on a map.

## What V0 does

- Tries to fetch official Basel-Stadt Open Data datasets:
  - `100042` statistical neighborhoods
  - `100029` school locations
  - `100120` geocoded traffic accidents
- Normalizes them into three entity types: `Area`, `School`, `Accident`.
- Derives relations: `IN_AREA`, `ADJACENT_TO`, `NEAR`.
- Builds an in-memory NetworkX graph.
- Exposes FastAPI endpoints.
- Serves a MapLibre browser UI for inspection and two simple graph analyses.
- Falls back to an explicitly synthetic fixture when live data cannot be fetched or parsed.

## Run it

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>.

API docs: <http://127.0.0.1:8000/docs>

Force the built-in demo fixture:

```bash
BASEL_GRAPH_FIXTURE=1 uvicorn app.main:app --reload
```

Windows PowerShell:

```powershell
$env:BASEL_GRAPH_FIXTURE="1"
uvicorn app.main:app --reload
```

## Test

```bash
pytest
```

## Why this architecture?

The graph complements GIS; it does not replace it. Geometry remains attached to nodes while relationships become reusable first-class objects. See `docs/CONCEPT.md` and `docs/ARCHITECTURE.md`.

## Current limitations

- Live Basel adapters are intentionally tolerant because public dataset schemas may evolve. Inspect `/health` to see whether `live` or `fixture` mode is active.
- `NEAR` currently uses straight-line haversine distance, not a walking street network.
- Accident download is capped at 1,000 records in V0.
- The frontend uses MapLibre assets and a demo basemap from the internet.
- No PostGIS, graph database, GTFS, LLM or GNN yet.

## Best next steps

1. Harden the three Basel adapters against the current official schemas and add cached snapshots.
2. Add a street network and replace straight-line proximity with network accessibility.
3. Add the structured `/query` endpoint that becomes the stable contract for later AI/MCP clients.
