import json
from pathlib import Path
import httpx
from .config import BASEL_API, DATASETS, RAW_DIR
from .fixtures import fixture_records


def _pick_geometry(record):
    for key in ("geo_shape", "geoshape", "geometry"):
        value = record.get(key)
        if isinstance(value, dict):
            if value.get("type") == "Feature":
                return value.get("geometry")
            if value.get("type") in {"Point","Polygon","MultiPolygon","LineString","MultiLineString"}:
                return value
    p = record.get("geo_point_2d")
    if isinstance(p, dict) and "lon" in p and "lat" in p:
        return {"type":"Point","coordinates":[p["lon"],p["lat"]]}
    return None


def _name(record, kind, idx):
    keys = {
        "areas": ("wohnviertel_name","name","bezeichnung"),
        "schools": ("standort","name","schulname"),
        "accidents": ("unfalltyp_de","unfalltyp","strasse"),
    }[kind]
    for k in keys:
        if record.get(k): return str(record[k])
    return f"{kind[:-1].title()} {idx}"


def normalize(kind, rows):
    result=[]
    for i,row in enumerate(rows):
        geom=_pick_geometry(row)
        if not geom: continue
        source_id = row.get("id") or row.get("wohnviertel_id") or row.get("id_schule") or row.get("unfall_id") or i
        result.append({
            "id": f"{kind[:-1]}:{source_id}",
            "type": {"areas":"Area","schools":"School","accidents":"Accident"}[kind],
            "name": _name(row,kind,i),
            "geometry": geom,
            "properties": {k:v for k,v in row.items() if k not in {"geo_shape","geoshape","geometry","geo_point_2d"}},
            "provenance": {"source":"data.bs.ch","dataset":DATASETS[kind],"source_id":str(source_id),"derived":False}
        })
    return result


def fetch_dataset(dataset_id, limit):
    url=f"{BASEL_API}/{dataset_id}/records"
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        r=client.get(url, params={"limit":limit})
        r.raise_for_status()
        payload=r.json()
        return payload.get("results",[])


def load_data(force_fixture=False):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if force_fixture:
        return fixture_records()
    try:
        raw={
            "areas": fetch_dataset(DATASETS["areas"], 100),
            "schools": fetch_dataset(DATASETS["schools"], 500),
            "accidents": fetch_dataset(DATASETS["accidents"], 1000),
        }
        for k,v in raw.items():
            (RAW_DIR/f"{k}.json").write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding="utf-8")
        normalized={k:normalize(k,v) for k,v in raw.items()}
        normalized["mode"]="live"
        if not all(normalized[k] for k in ("areas","schools","accidents")):
            raise RuntimeError("One or more live datasets returned no usable geometry")
        return normalized
    except Exception as exc:
        data=fixture_records()
        data["fallback_reason"]=str(exc)
        return data
