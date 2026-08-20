# Architecture

```text
Basel Open Data ---- app/ingest.py ---- entity records
                              |             |
                              v             v
                       entity fixture   entity graph
                                            |
Overpass / OSM ----- app/street.py ---- walking graph
       |                    |               |
       v                    v               +-- persistent ACCESS_POINT
processed JSON cache   street fixture       |
                                            v
                             WalkingAccessibilityService
                              Dijkstra by edge length
                                            |
                         FastAPI GeoJSON + MapLibre UI
```

The entity graph and routing graph are intentionally distinct. The entity graph stores reusable facts and structural derived relations. The routing graph is optimized for weighted path calculations. Point entities receive an `ACCESS_POINT` relation to a `StreetNode`, but reachability is computed per request and is not materialized as thousands of `REACHABLE` relations.

`WalkingAccessibilityService` is a mode-specific service with a small conceptual interface: origin, time, speed, and result. A future transit or multimodal service can implement the same contract without changing entity ingestion.

Both graphs live in memory and load once at process startup. The service pre-indexes point-entity access nodes once, avoiding an all-entities/all-street-nodes scan per request. This is appropriate for the initial Basel scope.

FastAPI also serves the static frontend, so V0.2 needs no Node build system. Shapely performs geometry intersection and display-buffer construction; NetworkX performs weighted routing.
