import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from typing import List, Optional

from .accessibility import WalkingAccessibilityService
from .analysis import CityAnalysis
from .config import (
    BASEL_CENTER,
    DEFAULT_WALKING_SPEED_KMH,
    MAX_SNAP_DISTANCE_M,
    STATIC_DIR,
)
from .data_quality import build_report, concise, read_report
from .errors import BaselGraphError, UnknownServiceError
from .graph import build_graph, centroid_coords, connect_street_access, neighbors, node_payload, subgraph
from .ingest import load_data
from .service_index import index_from_payload
from .service_model import (
    CATEGORY_COLORS,
    ESSENTIAL_CATEGORIES,
    ServiceCategory,
    category_label,
    parse_category,
)
from .service_sources import load_services
from .street_sources import load_street_network

FIXTURE_MODE = os.getenv("BASEL_GRAPH_FIXTURE", "0") == "1"

# Loaded once at import time from prepared caches: no downloads, no rebuilds.
DATA = load_data(force_fixture=FIXTURE_MODE)
GRAPH = build_graph(DATA)
STREETS = load_street_network(force_fixture=FIXTURE_MODE)
SERVICES = index_from_payload(load_services(force_fixture=FIXTURE_MODE), STREETS)
ACCESSIBILITY = WalkingAccessibilityService(STREETS, GRAPH, SERVICES)
ANALYSIS = CityAnalysis(STREETS, SERVICES, GRAPH)
connect_street_access(GRAPH, STREETS)
QUALITY = read_report() or build_report(STREETS, DATA, SERVICES)


def parse_categories(value: Optional[str]) -> Optional[List[ServiceCategory]]:
    """`?categories=grocery,pharmacy` -> typed categories, or None for all."""
    if not value:
        return None
    categories = [parse_category(raw.strip()) for raw in value.split(",") if raw.strip()]
    return categories or None

app = FastAPI(title="15-Minute Basel Spatial Graph", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(BaselGraphError)
def handle_domain_error(request: Request, exc: BaselGraphError):
    """Explainable failures become clean JSON, never a stack trace."""
    return JSONResponse(status_code=exc.status_code, content=exc.as_payload())


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    stats = STREETS.stats()
    return {
        "ok": True,
        "entities": {
            "mode": DATA.get("mode"),
            "source": DATA.get("source", "synthetic fixture"),
            "fallback_reason": DATA.get("fallback_reason"),
            "areas": len(DATA.get("areas", [])),
            "schools": len(DATA.get("schools", [])),
            "accidents": len(DATA.get("accidents", [])),
        },
        "streets": {
            "mode": stats["mode"],
            "source": stats["source"],
            "fallback_reason": STREETS.fallback_reason,
            "nodes": stats["nodes"],
            "edges": stats["edges"],
            "total_length_m": stats["total_length_m"],
            "crs": stats["crs"],
            "metric_crs": stats["metric_crs"],
            "place": STREETS.provenance.get("place"),
            "retrieved_at": STREETS.provenance.get("retrieved_at"),
            "cache_path": STREETS.provenance.get("cache_path"),
        },
        "services": {
            "mode": SERVICES.mode,
            "fallback_reason": SERVICES.fallback_reason,
            "total": len(SERVICES.services),
            "routable": sum(1 for s in SERVICES.services if s.is_routable),
            "generated_at": SERVICES.generated_at,
            "resnapped_at_startup": SERVICES.resnapped,
            "categories": {c.value: len(SERVICES.by_category.get(c, [])) for c in SERVICES.categories},
        },
        "data_quality": {
            "available": bool(QUALITY),
            "warning_count": len(QUALITY.get("warnings", [])) if QUALITY else 0,
            "generated_at": QUALITY.get("generated_at") if QUALITY else None,
        },
        "graph": {"nodes": GRAPH.number_of_nodes(), "edges": GRAPH.number_of_edges()},
        "map": {
            "center": list(BASEL_CENTER),
            "zoom": 13.4,
            "default_walking_speed_kmh": DEFAULT_WALKING_SPEED_KMH,
            "max_snap_distance_m": MAX_SNAP_DISTANCE_M,
        },
        "categories": [
            {
                "category": c.value,
                "label": category_label(c),
                "color": CATEGORY_COLORS.get(c),
                "essential": c in ESSENTIAL_CATEGORIES,
                "count": len(SERVICES.by_category.get(c, [])),
            }
            for c in SERVICES.categories
        ],
        # Kept for backwards compatibility with the V0.1 health consumers.
        "data_mode": DATA.get("mode"),
        "street_network_mode": stats["mode"],
    }


@app.get("/data/status", tags=["data"])
def data_status():
    """Concise generated data-quality report (see data/processed/data_quality.json)."""
    return concise(QUALITY)


# --- services ----------------------------------------------------------------
@app.get("/services", tags=["services"])
def services_summary():
    """Prepared service categories: counts, sources, labels and colours."""
    return SERVICES.summary()


@app.get("/services/geojson", tags=["services"])
def services_geojson(categories: Optional[str] = Query(None, description="comma-separated category ids")):
    """Every prepared service as one GeoJSON FeatureCollection, for the map."""
    return SERVICES.feature_collection(parse_categories(categories))


@app.get("/services/{category}", tags=["services"])
def services_of_category(category: str):
    """All prepared locations of one category as GeoJSON."""
    parsed = parse_categories(category)[0]
    SERVICES.of_category(parsed)  # raises UnknownCategoryError when unprepared
    return SERVICES.feature_collection([parsed])


@app.get("/services/{category}/{service_id}", tags=["services"])
def service_detail(category: str, service_id: str):
    """One service with its full provenance and network attachment."""
    service = SERVICES.get(service_id)
    if service is None or service.category is not parse_categories(category)[0]:
        raise UnknownServiceError(f"No prepared service '{service_id}' in category '{category}'.")
    return service.summary()


@app.get("/accessibility/walk")
def walking_accessibility(
    lat: float = Query(..., ge=-90, le=90, description="WGS84 latitude of the origin"),
    lon: float = Query(..., ge=-180, le=180, description="WGS84 longitude of the origin"),
    minutes: float = Query(15, gt=0, le=60),
    walking_speed_kmh: float = Query(DEFAULT_WALKING_SPEED_KMH, gt=0, le=12),
    include_straight_line: bool = Query(True, description="add the Euclidean comparison circle"),
    include_buffer: bool = Query(False, description="add a buffered polygon around the reachable network"),
    categories: Optional[str] = Query(None, description="comma-separated service categories; default all"),
    include_services: bool = Query(True, description="include the reachable-service profile"),
):
    """Everything reachable on foot from one origin: network, services, completeness."""
    return ACCESSIBILITY.calculate(
        lat, lon, minutes, walking_speed_kmh,
        include_straight_line=include_straight_line,
        include_buffer=include_buffer,
        categories=parse_categories(categories),
        include_services=include_services,
    )


@app.get("/accessibility/walk/services", tags=["accessibility"])
def walking_service_profile(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    minutes: float = Query(15, gt=0, le=60),
    walking_speed_kmh: float = Query(DEFAULT_WALKING_SPEED_KMH, gt=0, le=12),
    categories: Optional[str] = Query(None),
    include_items: bool = Query(False, description="list each reachable service, not just counts"),
):
    """The 15-minute profile alone: counts, nearest times and completeness, no geometry."""
    result = ACCESSIBILITY.calculate(
        lat, lon, minutes, walking_speed_kmh,
        categories=parse_categories(categories),
        include_service_items=include_items,
        include_geometry=False,
    )
    return {key: result[key] for key in (
        "origin", "snapped_origin", "minutes", "walking_speed_kmh", "network",
        "reachable_services", "completeness", "euclidean_vs_network", "notes", "provenance",
    )}


@app.get("/accessibility/walk/route", tags=["accessibility"])
def walking_route(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    service_id: str = Query(..., description="id from /services or a reachability result"),
    walking_speed_kmh: float = Query(DEFAULT_WALKING_SPEED_KMH, gt=0, le=12),
):
    """The shortest walking path from an origin to one service, as GeoJSON."""
    return ACCESSIBILITY.route_to_service(lat, lon, service_id, walking_speed_kmh)


@app.get("/entities/{entity_type}/{entity_id:path}/accessibility")
def entity_accessibility(
    entity_type: str,
    entity_id: str,
    mode: str = "walk",
    minutes: float = Query(15, gt=0, le=60),
    walking_speed_kmh: float = Query(DEFAULT_WALKING_SPEED_KMH, gt=0, le=12),
):
    if mode != "walk":
        raise HTTPException(400, "Only walking mode is available in V0.2")
    if entity_id not in GRAPH:
        raise HTTPException(404, "Entity not found")
    geometry = GRAPH.nodes[entity_id].get("geometry", {})
    if geometry.get("type") == "Point":
        lon, lat = geometry["coordinates"]
    else:
        lon, lat = centroid_coords(geometry)
    return ACCESSIBILITY.calculate(lat, lon, minutes, walking_speed_kmh)


@app.get("/entities/{entity_type}")
def entities(entity_type: str):
    t = {"areas": "Area", "schools": "School", "accidents": "Accident",
         "area": "Area", "school": "School", "accident": "Accident"}.get(entity_type.lower())
    if not t:
        raise HTTPException(404, "Unknown entity type")
    return [node_payload(GRAPH, n) for n, d in GRAPH.nodes(data=True) if d.get("type") == t]


@app.get("/entities/{entity_type}/{entity_id:path}")
def entity(entity_type: str, entity_id: str):
    if entity_id not in GRAPH:
        raise HTTPException(404, "Entity not found")
    return node_payload(GRAPH, entity_id)


@app.get("/graph/neighbors/{node_id:path}")
def graph_neighbors(node_id: str):
    if node_id not in GRAPH:
        raise HTTPException(404, "Node not found")
    return neighbors(GRAPH, node_id)


@app.get("/graph/subgraph/{node_id:path}")
def graph_subgraph(node_id: str, depth: int = Query(1, ge=1, le=4)):
    if node_id not in GRAPH:
        raise HTTPException(404, "Node not found")
    return subgraph(GRAPH, node_id, depth)


@app.get("/analysis/accessibility-gaps", tags=["analysis"])
def accessibility_gaps(
    category: str = Query("pharmacy", description="service category to test"),
    minutes: float = Query(15, gt=0, le=60),
    walking_speed_kmh: float = Query(DEFAULT_WALKING_SPEED_KMH, gt=0, le=12),
    limit: int = Query(25, ge=1, le=200, description="how many representative gap points to return"),
):
    """Invert the query: where in Basel is this category NOT reachable in time?

    Exploratory. Coverage is measured at walking-network nodes, not at
    residents — see the `method` field in the response.
    """
    return ANALYSIS.accessibility_gaps(category, minutes, walking_speed_kmh, limit)


@app.get("/analysis/areas-by-accidents")
def areas_by_accidents():
    rows = []
    for n, d in GRAPH.nodes(data=True):
        if d.get("type") != "Area":
            continue
        count = sum(1 for u, v, e in GRAPH.in_edges(n, data=True)
                    if e.get("type") == "IN_AREA" and GRAPH.nodes[u].get("type") == "Accident")
        rows.append({"id": n, "name": d.get("name"), "accident_count": count})
    return sorted(rows, key=lambda x: x["accident_count"], reverse=True)


@app.get("/analysis/schools-by-nearby-accidents")
def schools_by_nearby_accidents():
    rows = []
    for n, d in GRAPH.nodes(data=True):
        if d.get("type") != "School":
            continue
        incoming = [(u, e) for u, v, e in GRAPH.in_edges(n, data=True)
                    if e.get("type") == "NEAR" and GRAPH.nodes[u].get("type") == "Accident"]
        rows.append({"id": n, "name": d.get("name"), "nearby_accident_count": len(incoming),
                     "accidents": [{"id": u, "distance_m": e.get("distance_m")} for u, e in incoming]})
    return sorted(rows, key=lambda x: x["nearby_accident_count"], reverse=True)
