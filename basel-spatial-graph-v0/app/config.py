from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
STATIC_DIR = ROOT / "app" / "static"

BASEL_API = "https://data.bs.ch/api/explore/v2.1/catalog/datasets"
DATASETS = {
    "areas": "100042",
    "schools": "100029",
    "accidents": "100120",
}
