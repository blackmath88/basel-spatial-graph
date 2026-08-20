# Architecture

```text
Basel Open Data API
       |
       v
 app/ingest.py ---- fallback fixture
       |
       v
 normalized Python records + geometry
       |
       v
 app/graph.py (Shapely + NetworkX)
       |
       +------> graph analytics
       |
       v
 FastAPI app/main.py
       |
       +------> JSON API / OpenAPI
       |
       v
 MapLibre static browser UI
```

## Deliberate choices

- **FastAPI serves the frontend too**: no Node toolchain in V0.
- **NetworkX in memory**: enough to validate the relational model before choosing a graph database.
- **Shapely**: point-in-polygon and geometry operations.
- **No GeoPandas yet**: V0 avoids a heavy data-stack dependency until we need table-scale transformations. GeoPandas/GeoParquet is the natural next step.
- **City2Graph not yet hardwired**: V0 first makes the graph contract explicit. The next iteration can evaluate which builders from City2Graph replace custom relation code cleanly.
