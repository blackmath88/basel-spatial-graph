from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
STATIC_DIR = ROOT / "app" / "static"
STREET_CACHE = PROCESSED_DIR / "basel_walk_network.json"

BASEL_API = "https://data.bs.ch/api/explore/v2.1/catalog/datasets"
DATASETS = {
    "areas": "100042",
    "schools": "100029",
    "accidents": "100120",
}

# The adapter uses this pedestrian-friendly OpenStreetMap query when no cache exists.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
BASEL_BBOX = (47.519, 7.554, 47.589, 7.635)  # south, west, north, east
