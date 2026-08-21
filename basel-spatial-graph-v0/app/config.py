"""Central paths, data sources and walking-model defaults."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
STATIC_DIR = ROOT / "app" / "static"

# --- Basel entity datasets (Open Government Data Basel-Stadt) -----------------
BASEL_API = "https://data.bs.ch/api/explore/v2.1/catalog/datasets"
DATASETS = {
    "areas": "100042",
    "schools": "100029",
    "accidents": "100120",
}
# The Opendatasoft v2.1 API caps `limit` at 100, so ingestion pages through it.
ODS_PAGE_SIZE = 100
ENTITY_LIMITS = {
    "areas": 100,
    "schools": 500,
    # Accidents go back to 2011; the graph only needs a recent, workable slice.
    "accidents": int(os.getenv("BASEL_ACCIDENT_LIMIT", "1500")),
}

# Normalized entity cache, written by `python -m app.prepare_data`.
ENTITY_CACHE = PROCESSED_DIR / "basel_entities.json"

# Prepared service (POI) cache, written by `python -m app.prepare_data`.
SERVICE_CACHE = PROCESSED_DIR / "basel_services.json"
# Generated data-quality report.
DATA_QUALITY_REPORT = PROCESSED_DIR / "data_quality.json"
# Neighbourhood population by age group, written by `python -m app.prepare_data`.
POPULATION_CACHE = PROCESSED_DIR / "basel_population.json"
# How many recent years of population data to prepare (the source has 49).
POPULATION_YEARS = int(os.getenv("BASEL_POPULATION_YEARS", "10"))
# The heterogeneous spatial graph, written by `python -m app.prepare_spatial_graph`.
SPATIAL_GRAPH_CACHE = PROCESSED_DIR / "basel_spatial_graph.json"
# Manifest of the frozen snapshot committed to the repository. See app/snapshot.py.
SNAPSHOT_MANIFEST = PROCESSED_DIR / "SNAPSHOT.json"

# --- Travel modes ---------------------------------------------------------
# Cached, normalized networks. Written by `python -m app.prepare_data`.
WALK_NETWORK_CACHE = PROCESSED_DIR / "basel_walking_network.graphml"
BIKE_NETWORK_CACHE = PROCESSED_DIR / "basel_cycling_network.graphml"
NETWORK_CACHES = {"walk": WALK_NETWORK_CACHE, "bike": BIKE_NETWORK_CACHE}
# OSMnx network types per prepared network.
NETWORK_TYPES = {"walk": "walk", "bike": "bike"}
# OSMnx keeps its own HTTP response cache here so repeated prepares are cheap.
OSMNX_CACHE_DIR = RAW_DIR / "osmnx_cache"

# Place queries tried in order before falling back to the bounding box.
# The canton is the primary target: the Basel-Stadt service datasets cover
# Riehen and Bettingen too, and a city-only network would leave them unroutable.
BASEL_PLACE_QUERIES = (
    "Basel-Stadt, Switzerland",
    "Basel, Switzerland",
)
BASEL_BBOX = (47.5193, 7.5547, 47.6009, 7.6938)  # south, west, north, east

# CH1903+ / LV95: the official Swiss projected CRS, metres, correct for Basel.
METRIC_CRS = "EPSG:2056"
GEOGRAPHIC_CRS = "EPSG:4326"

# --- Public transport -------------------------------------------------------
# Official Swiss GTFS timetable. The permalink always resolves to the newest
# publication of the current annual feed.
GTFS_URL = "https://data.opentransportdata.swiss/dataset/timetable-2026-gtfs2020/permalink"
GTFS_DATASET_URL = "https://data.opentransportdata.swiss/dataset/timetable-2026-gtfs2020"
GTFS_ARCHIVE = RAW_DIR / "gtfs" / "gtfs_ch.zip"
TRANSIT_CACHE = PROCESSED_DIR / "basel_transit.npz"

# The prepared timetable area: Basel plus everything a short local journey can
# plausibly reach — Baselland to Liestal/Rheinfelden, Lörrach and Weil am Rhein
# in Germany, Saint-Louis in France. Deliberately wider than the canton, so a
# 30-minute journey is not cut off at an administrative border.
TRANSIT_BBOX = (47.42, 7.40, 47.68, 7.90)  # south, west, north, east

# Changing vehicles at the same stop costs this much platform time.
MIN_TRANSFER_SECONDS = int(os.getenv("BASEL_MIN_TRANSFER_SECONDS", "90"))
# Stops closer than this to each other are linked by a walking transfer.
STOP_TRANSFER_RADIUS_M = float(os.getenv("BASEL_STOP_TRANSFER_RADIUS_M", "300"))
DEFAULT_MAX_TRANSFERS = int(os.getenv("BASEL_MAX_TRANSFERS", "1"))

# --- Accessibility model -----------------------------------------------------
DEFAULT_WALKING_SPEED_KMH = float(os.getenv("BASEL_WALKING_SPEED_KMH", "4.8"))
# Prototype cycling speed: a flat average over the whole ride, no slope, no
# traffic stress, no surface penalty. See docs/CYCLING.md.
DEFAULT_CYCLING_SPEED_KMH = float(os.getenv("BASEL_CYCLING_SPEED_KMH", "15.0"))
DEFAULT_SPEEDS_KMH = {"walk": DEFAULT_WALKING_SPEED_KMH, "bike": DEFAULT_CYCLING_SPEED_KMH}
# A click further than this from any walkable node is reported as an error
# instead of silently snapping across the city.
MAX_SNAP_DISTANCE_M = float(os.getenv("BASEL_MAX_SNAP_DISTANCE_M", "1000"))
# A service further than this from any walkable node is flagged as a poor snap;
# beyond MAX_SERVICE_SNAP_M it is kept but excluded from routing.
POOR_SERVICE_SNAP_M = float(os.getenv("BASEL_POOR_SERVICE_SNAP_M", "150"))
MAX_SERVICE_SNAP_M = float(os.getenv("BASEL_MAX_SERVICE_SNAP_M", "500"))

# Half-width of the "reachable network" visual aid, in metres.
NETWORK_BUFFER_M = float(os.getenv("BASEL_NETWORK_BUFFER_M", "30"))

# Map viewport for the frontend.
BASEL_CENTER = (7.5895, 47.5570)  # lon, lat — between Barfüsserplatz and Basel SBB
