"""Basel entity ingestion (areas, schools, accidents) from data.bs.ch.

Live fetching happens in `python -m app.prepare_data`. The API server only
reads the prepared cache, or falls back to the fixture with a stated reason.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from .config import BASEL_API, DATASETS, ENTITY_CACHE, ENTITY_LIMITS, ODS_PAGE_SIZE, RAW_DIR
from .fixtures import fixture_records

GEOMETRY_KEYS = ("geo_shape", "geoshape", "geometry")
NAME_KEYS = {
    "areas": ("wov_name", "wov_label", "wohnviertel_name", "name", "bezeichnung"),
    "schools": ("sc_schulstandort", "sc_adresse", "standort", "name", "schulname"),
    "accidents": ("vu_typ", "unfalltyp_de", "unfalltyp", "vu_strassenart", "strasse"),
}
ID_KEYS = {
    "areas": ("wov_id", "wohnviertel_id", "id"),
    "schools": ("sc_id", "id_schule", "id"),
    "accidents": ("gml_id", "unfall_id", "id"),
}
ENTITY_TYPES = {"areas": "Area", "schools": "School", "accidents": "Accident"}
# Newest accidents first, so a truncated slice is still a meaningful one.
ORDER_BY = {"accidents": "vu_jahr desc"}


def _pick_geometry(record):
    for key in GEOMETRY_KEYS:
        value = record.get(key)
        if isinstance(value, dict):
            if value.get("type") == "Feature":
                return value.get("geometry")
            if value.get("type") in {"Point", "Polygon", "MultiPolygon", "LineString", "MultiLineString"}:
                return value
    p = record.get("geo_point_2d")
    if isinstance(p, dict) and "lon" in p and "lat" in p:
        return {"type": "Point", "coordinates": [p["lon"], p["lat"]]}
    return None


def _name(record, kind, idx):
    for key in NAME_KEYS[kind]:
        if record.get(key):
            return str(record[key])
    return f"{kind[:-1].title()} {idx}"


def _source_id(record, kind, idx, geometry=None):
    for key in ID_KEYS[kind]:
        if record.get(key) not in (None, ""):
            return str(record[key])
    # No natural key (e.g. school locations): derive a stable one from position,
    # so ids survive re-ingestion instead of shifting with row order.
    if geometry:
        digest = hashlib.sha1(json.dumps(geometry, sort_keys=True).encode()).hexdigest()
        return digest[:10]
    return str(idx)


def normalize(kind, rows):
    result = []
    for i, row in enumerate(rows):
        geom = _pick_geometry(row)
        if not geom:
            continue
        source_id = _source_id(row, kind, i, geom)
        result.append({
            "id": f"{kind[:-1]}:{source_id}",
            "type": ENTITY_TYPES[kind],
            "name": _name(row, kind, i),
            "geometry": geom,
            "properties": {k: v for k, v in row.items() if k not in {"geo_shape", "geoshape", "geometry", "geo_point_2d", "map_links"}},
            "provenance": {
                "source": "data.bs.ch",
                "dataset": DATASETS[kind],
                "source_id": source_id,
                "source_url": f"https://data.bs.ch/explore/dataset/{DATASETS[kind]}/",
                "license": "Open Government Data Basel-Stadt (CC BY 3.0 CH)",
                "derived": False,
            },
        })
    return result


def fetch_dataset(dataset_id, limit, order_by: Optional[str] = None,
                  where: Optional[str] = None, page_size: int = ODS_PAGE_SIZE):
    """Page through the Opendatasoft v2.1 API, which caps `limit` at 100."""
    rows = []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        while len(rows) < limit:
            params = {"limit": min(page_size, limit - len(rows)), "offset": len(rows)}
            if order_by:
                params["order_by"] = order_by
            if where:
                params["where"] = where
            response = client.get(f"{BASEL_API}/{dataset_id}/records", params=params)
            if response.status_code == 400 and order_by:
                order_by = None  # dataset does not expose that sort field
                continue
            response.raise_for_status()
            page = response.json().get("results", [])
            rows.extend(page)
            if len(page) < params["limit"]:
                break
    return rows


def fetch_entities(save_raw: bool = True) -> dict:
    """Fetch and normalize live entities. Raises on failure — the caller decides."""
    raw = {
        kind: fetch_dataset(DATASETS[kind], ENTITY_LIMITS[kind], ORDER_BY.get(kind))
        for kind in DATASETS
    }
    if save_raw:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        for kind, rows in raw.items():
            (RAW_DIR / f"{kind}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    normalized = {kind: normalize(kind, rows) for kind, rows in raw.items()}
    missing = [k for k in DATASETS if not normalized[k]]
    if missing:
        raise RuntimeError(f"Live datasets returned no usable geometry: {', '.join(missing)}")
    normalized["mode"] = "live"
    normalized["source"] = "data.bs.ch"
    return normalized


def write_entity_cache(data: dict, path=None):
    path = Path(path or ENTITY_CACHE)
    # Stamped so the snapshot manifest can state when these records were fetched.
    data.setdefault("generated_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def read_entity_cache(path=None) -> dict:
    path = Path(path or ENTITY_CACHE)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not all(data.get(kind) for kind in DATASETS):
        raise RuntimeError("Entity cache is incomplete")
    data.setdefault("mode", "live")
    data["cache_path"] = str(path)
    return data


def load_data(force_fixture: bool = False, path=None) -> dict:
    """Server-side load: cache only, never a live request on startup."""
    path = Path(path or ENTITY_CACHE)
    if force_fixture:
        return fixture_records()
    try:
        return read_entity_cache(path)
    except FileNotFoundError:
        data = fixture_records()
        data["fallback_reason"] = (
            f"No prepared entity cache at {path}. Run `python -m app.prepare_data`."
        )
        return data
    except Exception as exc:
        data = fixture_records()
        data["fallback_reason"] = str(exc)
        return data
