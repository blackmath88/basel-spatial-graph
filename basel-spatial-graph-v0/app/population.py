"""Neighbourhood population by age group, from Basel-Stadt Open Data.

This is the project's first genuinely statistical dimension: everything before
it was a place, a network or a destination. It is what makes a query like
"neighbourhoods with many children and poor pharmacy access" answerable.

Source: dataset `100128`, *Wohnbevölkerung nach Geschlecht, Alter,
Staatsangehörigkeit und Wohnviertel* — resident population by single year of
age per Wohnviertel, published annually. Age groups are aggregated here with
explicit, documented boundaries; nothing is interpolated, estimated or invented.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from .config import BASEL_API, POPULATION_CACHE, POPULATION_YEARS

DATASET = "100128"
DATASET_TITLE = "Wohnbevölkerung nach Geschlecht, Alter, Staatsangehörigkeit und Wohnviertel"
DATASET_URL = f"https://data.bs.ch/explore/dataset/{DATASET}/"
LICENSE = "Open Government Data Basel-Stadt (CC BY 3.0 CH)"

# Explicit, auditable age-group boundaries. `young` matches the cantonal
# Jugendquotient (0-19) and `elderly` the Altersquotient (65+), so the derived
# figures line up with Basel-Stadt's own published ratios.
AGE_GROUPS = {
    "total": {"where": None, "definition": "all residents"},
    "children": {"where": "person_alter < 18", "definition": "aged 0-17 (minors)"},
    "young": {"where": "person_alter < 20",
              "definition": "aged 0-19 (the cantonal Jugendquotient definition)"},
    "working_age": {"where": "person_alter >= 20 and person_alter < 65", "definition": "aged 20-64"},
    "elderly": {"where": "person_alter >= 65",
                "definition": "aged 65+ (the cantonal Altersquotient definition)"},
    "elderly_80_plus": {"where": "person_alter >= 80", "definition": "aged 80+"},
}


def _aggregate(year: str, where: Optional[str], client: httpx.Client) -> Dict[str, dict]:
    """One grouped query: population per Wohnviertel for one age filter.

    The Opendatasoft aggregation API does the counting server-side, so this
    reads 21 rows rather than the dataset's 370,000.
    """
    clause = f'jahr="{year}"' + (f" and {where}" if where else "")
    response = client.get(
        f"{BASEL_API}/{DATASET}/records",
        params={"select": "wohnviertel_id, wohnviertel, sum(anzahl) as n",
                "group_by": "wohnviertel_id, wohnviertel", "where": clause, "limit": 100},
    )
    response.raise_for_status()
    return {row["wohnviertel_id"]: row for row in response.json().get("results", [])}


def available_years(client: Optional[httpx.Client] = None) -> List[str]:
    owned = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    try:
        response = client.get(f"{BASEL_API}/{DATASET}/records",
                              params={"select": "jahr, sum(anzahl) as n", "group_by": "jahr",
                                      "order_by": "jahr desc", "limit": 50})
        response.raise_for_status()
        return [row["jahr"] for row in response.json().get("results", []) if row.get("jahr")]
    finally:
        if owned:
            client.close()


def fetch_population(years: Optional[List[str]] = None,
                     recent_years: int = POPULATION_YEARS) -> dict:
    """Fetch the most recent `recent_years` years. Raises on failure.

    The dataset reaches back to 1974. Keeping the last decade is enough for the
    questions this graph answers and keeps preparation to a few seconds; the
    year dimension stays explicit either way. Pass `years` to override.
    """
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        years = years or available_years(client)[:max(1, recent_years)]
        if not years:
            raise RuntimeError(f"data.bs.ch dataset {DATASET} reported no years")
        observations: List[dict] = []
        for year in sorted(years):
            groups = {name: _aggregate(year, spec["where"], client)
                      for name, spec in AGE_GROUPS.items()}
            for wov_id, row in sorted(groups["total"].items()):
                observations.append({
                    "wov_id": wov_id,
                    "name": row["wohnviertel"],
                    "year": int(year),
                    **{name: int(groups[name].get(wov_id, {}).get("n") or 0)
                       for name in AGE_GROUPS},
                })
    if not observations:
        raise RuntimeError(f"data.bs.ch dataset {DATASET} returned no usable rows")
    return {
        "mode": "live",
        "observations": observations,
        "years": sorted({row["year"] for row in observations}),
        "latest_year": max(row["year"] for row in observations),
        "provenance": {
            "source": "Open Government Data Basel-Stadt (data.bs.ch)",
            "dataset": DATASET,
            "dataset_title": DATASET_TITLE,
            "source_url": DATASET_URL,
            "license": LICENSE,
            "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "spatial_unit": "Wohnviertel (statistical neighbourhood)",
            "age_group_definitions": {k: v["definition"] for k, v in AGE_GROUPS.items()},
            "method": "server-side aggregation of single-year-of-age counts; no estimation",
            "years_prepared": sorted({row["year"] for row in observations}),
            "note": ("The dataset reaches back to 1974; the most recent years are prepared. "
                     "Re-run preparation with a different range to widen it."),
            "derived": False,
        },
    }


def write_cache(data: dict, path=None) -> Path:
    path = Path(path or POPULATION_CACHE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def read_cache(path=None) -> dict:
    path = Path(path or POPULATION_CACHE)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("observations"):
        raise RuntimeError("Population cache is empty")
    data["cache_path"] = str(path)
    return data


def fixture_population() -> dict:
    """Deterministic stand-in. Clearly synthetic; never presented as real."""
    rows = [
        {"wov_id": "a", "name": "Fixture West", "year": 2024,
         "total": 10000, "children": 2000, "young": 2200, "working_age": 6000,
         "elderly": 2000, "elderly_80_plus": 500},
        {"wov_id": "a", "name": "Fixture West", "year": 2025,
         "total": 10200, "children": 2100, "young": 2300, "working_age": 6000,
         "elderly": 2100, "elderly_80_plus": 520},
        {"wov_id": "b", "name": "Fixture East", "year": 2024,
         "total": 4000, "children": 400, "young": 450, "working_age": 2600,
         "elderly": 1000, "elderly_80_plus": 260},
        {"wov_id": "b", "name": "Fixture East", "year": 2025,
         "total": 4100, "children": 420, "young": 470, "working_age": 2650,
         "elderly": 1030, "elderly_80_plus": 270},
    ]
    return {
        "mode": "fixture",
        "observations": rows,
        "years": [2024, 2025],
        "latest_year": 2025,
        "provenance": {
            "source": "synthetic fixture",
            "dataset": "fixture",
            "dataset_title": "Synthetic neighbourhood population",
            "license": "fixture-only; not real observations",
            "retrieved_at": "2020-01-01T00:00:00+00:00",
            "spatial_unit": "fixture neighbourhood",
            "age_group_definitions": {k: v["definition"] for k, v in AGE_GROUPS.items()},
            "method": "hand-written",
            "derived": False,
        },
    }


def load_population(force_fixture: bool = False, path=None) -> dict:
    """Server-side load: cache only, never a live request on startup."""
    if force_fixture:
        return fixture_population()
    try:
        data = read_cache(path)
        data.setdefault("mode", "live")
        data["fallback_reason"] = None
        return data
    except FileNotFoundError:
        data = fixture_population()
        data["fallback_reason"] = (
            f"No prepared population cache at {Path(path or POPULATION_CACHE)}. "
            "Run `python -m app.prepare_data`."
        )
        return data
    except Exception as exc:
        data = fixture_population()
        data["fallback_reason"] = str(exc)
        return data
