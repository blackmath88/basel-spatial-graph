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

# --- Walking network ---------------------------------------------------------
# Cached, normalized pedestrian network. Written by `python -m app.prepare_data`.
WALK_NETWORK_CACHE = PROCESSED_DIR / "basel_walking_network.graphml"
# OSMnx keeps its own HTTP response cache here so repeated prepares are cheap.
OSMNX_CACHE_DIR = RAW_DIR / "osmnx_cache"

# Place queries tried in order before falling back to the bounding box.
BASEL_PLACE_QUERIES = (
    "Basel, Switzerland",
    "Basel-Stadt, Switzerland",
)
BASEL_BBOX = (47.519, 7.554, 47.589, 7.635)  # south, west, north, east
OSMNX_NETWORK_TYPE = "walk"

# CH1903+ / LV95: the official Swiss projected CRS, metres, correct for Basel.
METRIC_CRS = "EPSG:2056"
GEOGRAPHIC_CRS = "EPSG:4326"

# --- Accessibility model -----------------------------------------------------
DEFAULT_WALKING_SPEED_KMH = float(os.getenv("BASEL_WALKING_SPEED_KMH", "4.8"))
# A click further than this from any walkable node is reported as an error
# instead of silently snapping across the city.
MAX_SNAP_DISTANCE_M = float(os.getenv("BASEL_MAX_SNAP_DISTANCE_M", "1000"))
# Half-width of the "reachable network" visual aid, in metres.
NETWORK_BUFFER_M = float(os.getenv("BASEL_NETWORK_BUFFER_M", "30"))

# Map viewport for the frontend.
BASEL_CENTER = (7.5895, 47.5570)  # lon, lat — between Barfüsserplatz and Basel SBB
