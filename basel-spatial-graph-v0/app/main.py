import os

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from typing import List, Optional

from .accessibility import CyclingAccessibilityService, WalkingAccessibilityService
from .analysis import CityAnalysis
from .config import (
    BASEL_CENTER,
    DEFAULT_CYCLING_SPEED_KMH,
    DEFAULT_SPEEDS_KMH,
    DEFAULT_WALKING_SPEED_KMH,
    MAX_SNAP_DISTANCE_M,
    STATIC_DIR,
)
from .data_quality import build_report, concise, read_report
from .errors import (
    BaselGraphError,
    QuerySpecError,
    SpatialGraphUnavailableError,
    TransitUnavailableError,
    UnknownServiceError,
)
from .transit_model import route_type_label
from .modes import MODE_COLORS, MODE_ORDER, TravelMode, mode_label, parse_mode
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
from .multimodal import DEFAULT_MAX_TRANSFERS, MAX_TRANSFERS_LIMIT, MultimodalAccessibilityService
from .service_sources import load_services
from .snapshot import runtime_snapshot
from .spatial_graph import SpatialGraphService
from .street_sources import load_network
from .transit_sources import load_transit

FIXTURE_MODE = os.getenv("BASEL_GRAPH_FIXTURE", "0") == "1"
# Detailed rows returned per category. Every reachable service id is always
# listed under `ids`; this only caps the verbose rows the sidebar shows.
SERVICE_ITEM_LIMIT = 60

# Loaded once at import time from prepared caches: no downloads, no rebuilds.
DATA = load_data(force_fixture=FIXTURE_MODE)
GRAPH = build_graph(DATA)
NETWORKS = {
    "walk": load_network("walk", force_fixture=FIXTURE_MODE),
    "bike": load_network("bike", force_fixture=FIXTURE_MODE),
}
STREETS = NETWORKS["walk"]          # the V0.2/V0.3 name
BIKE_NETWORK = NETWORKS["bike"]
SERVICES = index_from_payload(load_services(force_fixture=FIXTURE_MODE), NETWORKS)

ACCESSIBILITY = WalkingAccessibilityService(STREETS, GRAPH, SERVICES)
CYCLING = CyclingAccessibilityService(BIKE_NETWORK, GRAPH, SERVICES)
SERVICES_BY_MODE = {TravelMode.WALK: ACCESSIBILITY, TravelMode.BIKE: CYCLING}

TRANSIT = load_transit(force_fixture=FIXTURE_MODE).attach_to_network(STREETS)
MULTIMODAL = MultimodalAccessibilityService(STREETS, TRANSIT, GRAPH, SERVICES)
if MULTIMODAL.available:
    SERVICES_BY_MODE[TravelMode.TRANSIT] = MULTIMODAL

ANALYSIS = CityAnalysis(STREETS, SERVICES, GRAPH)
connect_street_access(GRAPH, STREETS)
QUALITY = read_report() or build_report(NETWORKS, DATA, SERVICES, TRANSIT)

# The heterogeneous graph is optional: the reference application works without
# it, and it is only present once `python -m app.prepare_spatial_graph` has run.
# In fixture mode it is built in memory, so a synthetic server never answers
# from a graph whose coordinates its engines do not recognise.
if FIXTURE_MODE:
    from .spatial_graph.fixtures import fixture_graph as _fixture_graph

    SPATIAL_GRAPH = SpatialGraphService(_fixture_graph()[0], engines=SERVICES_BY_MODE)
else:
    SPATIAL_GRAPH = SpatialGraphService.load(engines=SERVICES_BY_MODE)


# Which of the three data states each subsystem is running on: the committed
# frozen snapshot, something prepared locally since, or the synthetic fixture.
DATA_MODES = {
    "entities": DATA.get("mode"),
    "walk": STREETS.mode,
    "bike": BIKE_NETWORK.mode,
    "services": SERVICES.mode,
    "transit": TRANSIT.mode,
    "spatial_graph": (SPATIAL_GRAPH.graph.metadata.get("mode")
                      if SPATIAL_GRAPH else "fixture" if FIXTURE_MODE else None),
}
SNAPSHOT = runtime_snapshot()


def data_state(key: str) -> dict:
    """The `data_state` block one subsystem reports."""
    return SNAPSHOT.block(key, DATA_MODES.get(key))


def spatial_graph() -> SpatialGraphService:
    if SPATIAL_GRAPH is None:
        raise SpatialGraphUnavailableError(
            "No spatial graph is prepared. Run `python -m app.prepare_spatial_graph`.")
    return SPATIAL_GRAPH


def payload(result: dict) -> JSONResponse:
    """Serialize a large result directly.

    FastAPI's default path runs `jsonable_encoder` over the whole structure
    first, which costs more than the JSON encoding itself on a two-megabyte
    GeoJSON response. These payloads are already plain JSON types.
    """
    return JSONResponse(content=result)


def service_for(mode: TravelMode):
    """The accessibility service that answers for one travel mode."""
    engine = SERVICES_BY_MODE.get(mode)
    if engine is None:
        raise TransitUnavailableError(
            "Transit routing is not available in this build.", mode=mode.value)
    return engine


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
    bike_stats = BIKE_NETWORK.stats()
    return {
        "ok": True,
        "snapshot": SNAPSHOT.describe(DATA_MODES),
        "entities": {
            "mode": DATA.get("mode"),
            "data_state": data_state("entities"),
            "source": DATA.get("source", "synthetic fixture"),
            "fallback_reason": DATA.get("fallback_reason"),
            "areas": len(DATA.get("areas", [])),
            "schools": len(DATA.get("schools", [])),
            "accidents": len(DATA.get("accidents", [])),
        },
        "streets": {
            "mode": stats["mode"],
            "data_state": data_state("walk"),
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
        "bike": {
            "mode": bike_stats["mode"],
            "data_state": data_state("bike"),
            "source": bike_stats["source"],
            "fallback_reason": BIKE_NETWORK.fallback_reason,
            "nodes": bike_stats["nodes"],
            "edges": bike_stats["edges"],
            "total_length_m": bike_stats["total_length_m"],
            "place": BIKE_NETWORK.provenance.get("place"),
            "retrieved_at": BIKE_NETWORK.provenance.get("retrieved_at"),
            "cache_path": BIKE_NETWORK.provenance.get("cache_path"),
        },
        "services": {
            "mode": SERVICES.mode,
            "data_state": data_state("services"),
            "fallback_reason": SERVICES.fallback_reason,
            "total": len(SERVICES.services),
            "routable": sum(1 for s in SERVICES.services if s.is_routable),
            "generated_at": SERVICES.generated_at,
            "resnapped_at_startup": SERVICES.resnapped,
            "categories": {c.value: len(SERVICES.by_category.get(c, [])) for c in SERVICES.categories},
        },
        "transit": {
            "mode": TRANSIT.mode,
            "data_state": data_state("transit"),
            "available": MULTIMODAL.available,
            "fallback_reason": TRANSIT.fallback_reason,
            "source": TRANSIT.provenance.get("source"),
            "feed": TRANSIT.provenance.get("feed"),
            "feed_version": TRANSIT.provenance.get("feed_version"),
            "stops": TRANSIT.timetable.stop_count if TRANSIT.timetable else 0,
            "routes": TRANSIT.timetable.route_count if TRANSIT.timetable else 0,
            "trips": TRANSIT.timetable.trip_count if TRANSIT.timetable else 0,
            "stops_attached": sum(1 for a in TRANSIT.stop_access if a.is_routable),
            "default_max_transfers": DEFAULT_MAX_TRANSFERS,
            "timezone": "Europe/Zurich",
        },
        "spatial_graph": {
            "available": SPATIAL_GRAPH is not None,
            "mode": SPATIAL_GRAPH.graph.metadata.get("mode") if SPATIAL_GRAPH else None,
            "data_state": data_state("spatial_graph"),
            "nodes": SPATIAL_GRAPH.graph.graph.number_of_nodes() if SPATIAL_GRAPH else 0,
            "edges": SPATIAL_GRAPH.graph.graph.number_of_edges() if SPATIAL_GRAPH else 0,
            "generated_at": SPATIAL_GRAPH.graph.metadata.get("generated_at") if SPATIAL_GRAPH else None,
            "population_reference_year": (SPATIAL_GRAPH.graph.metadata.get("population_reference_year")
                                          if SPATIAL_GRAPH else None),
        },
        "data_quality": {
            "available": bool(QUALITY),
            "data_state": data_state("data_quality"),
            "warning_count": len(QUALITY.get("warnings", [])) if QUALITY else 0,
            "generated_at": QUALITY.get("generated_at") if QUALITY else None,
        },
        "graph": {"nodes": GRAPH.number_of_nodes(), "edges": GRAPH.number_of_edges()},
        "map": {
            "center": list(BASEL_CENTER),
            "zoom": 13.4,
            "default_walking_speed_kmh": DEFAULT_WALKING_SPEED_KMH,
            "default_cycling_speed_kmh": DEFAULT_CYCLING_SPEED_KMH,
            "max_snap_distance_m": MAX_SNAP_DISTANCE_M,
        },
        "modes": [
            {
                "mode": m.value,
                "label": mode_label(m),
                "color": MODE_COLORS[m],
                "available": m in SERVICES_BY_MODE,
                "default_speed_kmh": DEFAULT_SPEEDS_KMH.get(m.value),
            }
            for m in MODE_ORDER
        ],
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
    return {"snapshot": SNAPSHOT.describe(DATA_MODES), **concise(QUALITY)}


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
    return payload(ACCESSIBILITY.calculate(
        lat, lon, minutes, walking_speed_kmh,
        include_straight_line=include_straight_line,
        include_buffer=include_buffer,
        categories=parse_categories(categories),
        include_services=include_services,
    ))


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


@app.get("/accessibility", tags=["accessibility"])
def accessibility(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    mode: str = Query("walk", description="walk | bike | transit"),
    minutes: float = Query(15, gt=0, le=60),
    speed_kmh: Optional[float] = Query(None, description="overrides the mode default"),
    departure_time: Optional[str] = Query(None, description="transit only: HH:MM or ISO datetime"),
    max_transfers: int = Query(DEFAULT_MAX_TRANSFERS, ge=0, le=MAX_TRANSFERS_LIMIT),
    categories: Optional[str] = Query(None),
    include_services: bool = Query(True),
    include_straight_line: bool = Query(True),
    service_limit: int = Query(SERVICE_ITEM_LIMIT, ge=1, le=2000,
                               description="detailed rows per category; every reachable id is always listed"),
):
    """One origin, one time budget, one travel mode — everything reachable."""
    travel_mode = parse_mode(mode)
    engine = service_for(travel_mode)
    wanted = parse_categories(categories)
    if travel_mode is TravelMode.TRANSIT:
        return payload(engine.calculate(
            lat, lon, minutes, departure_time=departure_time,
            max_transfers=max_transfers, walking_speed_kmh=speed_kmh,
            categories=wanted, include_services=include_services,
            service_limit=service_limit))
    return payload(engine.calculate(
        lat, lon, minutes, speed_kmh, categories=wanted,
        include_services=include_services,
        include_straight_line=include_straight_line,
        service_limit=service_limit))


@app.get("/accessibility/bike", tags=["accessibility"])
def cycling_accessibility(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    minutes: float = Query(15, gt=0, le=60),
    cycling_speed_kmh: float = Query(DEFAULT_CYCLING_SPEED_KMH, gt=0, le=45),
    include_straight_line: bool = Query(True),
    include_buffer: bool = Query(False),
    categories: Optional[str] = Query(None),
    include_services: bool = Query(True),
    service_limit: int = Query(SERVICE_ITEM_LIMIT, ge=1, le=2000),
):
    """Reachability over the bicycle-accessible network at a flat speed."""
    return payload(CYCLING.calculate(
        lat, lon, minutes, cycling_speed_kmh,
        include_straight_line=include_straight_line, include_buffer=include_buffer,
        categories=parse_categories(categories), include_services=include_services,
        service_limit=service_limit,
    ))


@app.get("/accessibility/bike/route", tags=["accessibility"])
def cycling_route(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    service_id: str = Query(...),
    cycling_speed_kmh: float = Query(DEFAULT_CYCLING_SPEED_KMH, gt=0, le=45),
):
    """The shortest bicycle path from an origin to one service."""
    return CYCLING.route_to_service(lat, lon, service_id, cycling_speed_kmh)


@app.get("/accessibility/transit", tags=["accessibility"])
def transit_accessibility(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    minutes: float = Query(15, gt=0, le=60),
    departure_time: Optional[str] = Query(None, description="HH:MM or ISO datetime, Europe/Zurich"),
    max_transfers: int = Query(DEFAULT_MAX_TRANSFERS, ge=0, le=MAX_TRANSFERS_LIMIT),
    walking_speed_kmh: float = Query(DEFAULT_WALKING_SPEED_KMH, gt=0, le=12),
    categories: Optional[str] = Query(None),
    include_services: bool = Query(True),
    service_limit: int = Query(SERVICE_ITEM_LIMIT, ge=1, le=2000),
):
    """Walk → wait → ride → (transfer) → walk, against the real timetable."""
    return payload(MULTIMODAL.calculate(
        lat, lon, minutes, departure_time=departure_time, max_transfers=max_transfers,
        walking_speed_kmh=walking_speed_kmh, categories=parse_categories(categories),
        include_services=include_services, service_limit=service_limit,
    ))


@app.get("/accessibility/transit/route", tags=["accessibility"])
def transit_route(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    service_id: str = Query(...),
    minutes: float = Query(60, gt=0, le=120),
    departure_time: Optional[str] = Query(None),
    max_transfers: int = Query(DEFAULT_MAX_TRANSFERS, ge=0, le=MAX_TRANSFERS_LIMIT),
    walking_speed_kmh: float = Query(DEFAULT_WALKING_SPEED_KMH, gt=0, le=12),
):
    """One readable itinerary to one service, with its geometry."""
    return MULTIMODAL.route_to_service(lat, lon, service_id, minutes=minutes,
                                       departure_time=departure_time,
                                       max_transfers=max_transfers,
                                       walking_speed_kmh=walking_speed_kmh)


@app.get("/accessibility/compare", tags=["accessibility"])
def compare_modes(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    minutes: float = Query(15, gt=0, le=60),
    departure_time: Optional[str] = Query(None),
    max_transfers: int = Query(DEFAULT_MAX_TRANSFERS, ge=0, le=MAX_TRANSFERS_LIMIT),
    modes: Optional[str] = Query(None, description="comma-separated; default all available"),
):
    """The same question answered by every mode, side by side.

    Geometry is omitted: this is the comparison table, not the map.
    """
    wanted = [parse_mode(m.strip()) for m in modes.split(",")] if modes else list(MODE_ORDER)
    rows, errors = {}, {}
    for travel_mode in wanted:
        engine = SERVICES_BY_MODE.get(travel_mode)
        if engine is None:
            errors[travel_mode.value] = "not available in this build"
            continue
        try:
            if travel_mode is TravelMode.TRANSIT:
                result = engine.calculate(lat, lon, minutes, departure_time=departure_time,
                                          max_transfers=max_transfers,
                                          include_service_items=False, include_geometry=False)
            else:
                result = engine.calculate(lat, lon, minutes, include_service_items=False,
                                          include_geometry=False, include_straight_line=False)
        except BaselGraphError as exc:
            errors[travel_mode.value] = exc.message
            continue
        rows[travel_mode.value] = {
            "mode": travel_mode.value,
            "label": mode_label(travel_mode),
            "color": MODE_COLORS[travel_mode],
            "speed_kmh": result.get("speed_kmh"),
            "reachable_services": {
                name: {"count": row["count"], "nearest_minutes": row["nearest_minutes"],
                       "nearest_name": row["nearest_name"], "label": row["label"],
                       "essential": row["essential"]}
                for name, row in result["reachable_services"].items()
            },
            "completeness": result["completeness"],
            "network": result["network"],
            "transit": result.get("transit"),
            "departure_time": result.get("departure_time"),
            "service_date": result.get("service_date"),
            "notes": result.get("notes", []),
        }
    categories = sorted({name for row in rows.values() for name in row["reachable_services"]})
    return {
        "origin": {"lat": round(float(lat), 6), "lon": round(float(lon), 6)},
        "minutes": minutes,
        "modes": rows,
        "unavailable": errors,
        "categories": categories,
        # A ready-made table: category -> mode -> count.
        "table": {
            name: {mode: rows[mode]["reachable_services"].get(name, {}).get("count", 0)
                   for mode in rows}
            for name in categories
        },
    }


# --- transit reference data --------------------------------------------------
@app.get("/transit/status", tags=["transit"])
def transit_status():
    """Is a timetable prepared, where did it come from, and what does it cover?"""
    report = TRANSIT.quality_report()
    report["available"] = MULTIMODAL.available
    report["provenance"] = TRANSIT.provenance
    return report


@app.get("/transit/stops", tags=["transit"])
def transit_stops(
    limit: int = Query(500, ge=1, le=5000),
    attached_only: bool = Query(True, description="only stops the walking network can reach"),
    q: Optional[str] = Query(None, description="filter by name"),
):
    """Prepared stops as GeoJSON."""
    if not MULTIMODAL.available:
        raise TransitUnavailableError("No timetable is prepared.")
    table = TRANSIT.timetable
    needle = (q or "").strip().lower()
    features = []
    for index in range(table.stop_count):
        access = TRANSIT.stop_access[index]
        if attached_only and not access.is_routable:
            continue
        name = table.stop_names[index]
        if needle and needle not in name.lower():
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [
                round(float(table.stop_lon[index]), 6), round(float(table.stop_lat[index]), 6)]},
            "properties": {"id": table.stop_ids[index], "name": name,
                           "access": access.to_dict()},
        })
        if len(features) >= limit:
            break
    return {"type": "FeatureCollection", "features": features,
            "total": table.stop_count, "returned": len(features)}


@app.get("/transit/routes", tags=["transit"])
def transit_routes():
    """Prepared routes, grouped by vehicle type."""
    if not MULTIMODAL.available:
        raise TransitUnavailableError("No timetable is prepared.")
    table = TRANSIT.timetable
    rows = [
        {"id": r.id, "short_name": r.short_name, "long_name": r.long_name,
         "route_type": r.route_type, "vehicle": route_type_label(r.route_type),
         "label": r.label, "agency": r.agency_name}
        for r in table.routes
    ]
    rows.sort(key=lambda r: (r["vehicle"], r["short_name"]))
    counts = {}
    for row in rows:
        counts[row["vehicle"]] = counts.get(row["vehicle"], 0) + 1
    return {"total": len(rows), "by_vehicle": counts, "routes": rows}


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


# --- Spatial Graph Core ------------------------------------------------------
@app.get("/spatial-graph/status", tags=["spatial-graph"])
def spatial_graph_status():
    """What is loaded, when it was built, and which sources it came from."""
    return spatial_graph().status()


@app.get("/spatial-graph/schema", tags=["spatial-graph"])
def spatial_graph_schema():
    """The whole machine-readable schema: entity types, relations, operators, analyses."""
    return spatial_graph().schema()


@app.get("/spatial-graph/entity-types", tags=["spatial-graph"])
def spatial_graph_entity_types():
    return spatial_graph().entity_types()


@app.get("/spatial-graph/relation-types", tags=["spatial-graph"])
def spatial_graph_relation_types():
    return spatial_graph().relation_types()


@app.get("/spatial-graph/questions", tags=["spatial-graph"])
def spatial_graph_questions():
    """The standing cross-domain questions this graph can answer."""
    from .spatial_graph.questions import QUESTIONS

    return {
        "questions": [
            {"name": name,
             "summary": (function.__doc__ or "").strip().splitlines()[0],
             "parameters": [p for p in function.__code__.co_varnames[:function.__code__.co_argcount]
                            if p not in {"graph", "analysis"}]}
            for name, function in QUESTIONS.items()
        ]
    }


@app.get("/spatial-graph/questions/{name}", tags=["spatial-graph"])
def spatial_graph_question(
    name: str,
    category: Optional[str] = Query(None),
    mode: Optional[str] = Query(None, description="walk | bike | transit"),
    minutes: Optional[float] = Query(None, gt=0, le=60),
    limit: Optional[int] = Query(None, ge=1, le=100),
    min_children: Optional[int] = Query(None, ge=0),
    departure_time: Optional[str] = Query(None),
):
    """Run one standing question. Every answer states its own methodology."""
    import inspect

    from .spatial_graph.questions import QUESTIONS

    if name not in QUESTIONS:
        raise QuerySpecError(f"Unknown question '{name}'.", known=sorted(QUESTIONS))
    wanted = {"category": category, "mode": mode, "minutes": minutes, "limit": limit,
              "min_children": min_children, "departure_time": departure_time}
    accepted = inspect.signature(QUESTIONS[name]).parameters
    params = {k: v for k, v in wanted.items() if v is not None and k in accepted}
    return payload(spatial_graph().ask(name, **params))


@app.get("/spatial-graph/entities/{type_name}", tags=["spatial-graph"])
def spatial_graph_entities(
    type_name: str,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    include_geometry: bool = Query(False),
):
    """List entities of one type. Geometry is omitted unless asked for."""
    return payload(spatial_graph().entities(type_name, limit=limit, offset=offset,
                                            include_geometry=include_geometry))


@app.get("/spatial-graph/entities/{type_name}/{entity_id}", tags=["spatial-graph"])
def spatial_graph_entity(type_name: str, entity_id: str,
                         include_geometry: bool = Query(False)):
    return payload(spatial_graph().entity(type_name, entity_id, include_geometry))


@app.get("/spatial-graph/entities/{type_name}/{entity_id}/neighbors", tags=["spatial-graph"])
def spatial_graph_neighbors(
    type_name: str, entity_id: str,
    relation: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    include_geometry: bool = Query(False),
):
    return payload(spatial_graph().neighbors(type_name, entity_id, relation=relation,
                                             target_type=target_type, limit=limit,
                                             include_geometry=include_geometry))


@app.get("/spatial-graph/entities/{type_name}/{entity_id}/subgraph", tags=["spatial-graph"])
def spatial_graph_subgraph(
    type_name: str, entity_id: str,
    depth: int = Query(2, ge=1, le=4),
    relations: Optional[str] = Query(None, description="comma-separated relation names"),
    limit: int = Query(200, ge=1, le=1000),
    include_geometry: bool = Query(False),
):
    wanted = [r.strip() for r in relations.split(",") if r.strip()] if relations else None
    return payload(spatial_graph().subgraph(type_name, entity_id, depth=depth,
                                            relations=wanted, limit=limit,
                                            include_geometry=include_geometry))


@app.post("/spatial-graph/query", tags=["spatial-graph"])
def spatial_graph_query(spec: dict = Body(..., description="A query specification")):
    """Run a bounded relational query. See docs/QUERY_API.md for the grammar."""
    return payload(spatial_graph().query(spec))


@app.get("/spatial-graph/provenance/{entity_id}", tags=["spatial-graph"])
def spatial_graph_provenance(entity_id: str):
    """Where one entity — or one relation type — came from."""
    return spatial_graph().provenance(entity_id)


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
