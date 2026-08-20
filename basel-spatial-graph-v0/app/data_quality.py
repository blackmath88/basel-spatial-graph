"""Generated data-quality report for the prepared graph.

Written to `data/processed/data_quality.json` by `python -m app.prepare_data`
and exposed in a concise form at `/data/status`. As the graph grows, this is
what tells you whether a category is genuinely thin or merely badly snapped.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from .config import DATA_QUALITY_REPORT, POOR_SERVICE_SNAP_M
from .service_model import ServiceCategory, category_label
from .service_sources import duplicate_candidates

MAX_DUPLICATE_SAMPLES = 10


def build_report(networks=None, entities: Optional[dict] = None, service_index=None,
                 transit=None) -> dict:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "warnings": [],
    }
    warnings = report["warnings"]

    if networks is not None and not isinstance(networks, dict):
        networks = {"walk": networks}   # a single StreetNetwork, the V0.3 call
    for name, streets in (networks or {}).items():
        stats = streets.stats()
        block = {
            "network": name,
            "mode": stats["mode"],
            "source": stats["source"],
            "nodes": stats["nodes"],
            "edges": stats["edges"],
            "total_length_km": round(stats["total_length_m"] / 1000, 1),
            "crs": stats["crs"],
            "metric_crs": stats["metric_crs"],
            "dropped_edges": stats["dropped_edges"],
            "retrieved_at": streets.provenance.get("retrieved_at"),
            "fallback_reason": streets.fallback_reason,
        }
        report.setdefault("networks", {})[name] = block
        if name == "walk":
            report["network"] = block   # the V0.3 key
        if stats["mode"] != "live":
            warnings.append(f"{name.title()} network is FIXTURE data, not real Basel geography.")
        if stats["dropped_edges"]:
            warnings.append(
                f"{stats['dropped_edges']} {name} network edges had no usable length and were dropped.")

    if entities is not None:
        report["entities"] = {
            "mode": entities.get("mode"),
            "source": entities.get("source", "synthetic fixture"),
            "fallback_reason": entities.get("fallback_reason"),
            "counts": {k: len(entities.get(k, [])) for k in ("areas", "schools", "accidents")},
        }
        if entities.get("mode") != "live":
            warnings.append("Basel entity data is FIXTURE data.")

    if service_index is not None:
        report["services"] = _service_report(service_index, warnings)

    if transit is not None:
        report["transit"] = _transit_report(transit, warnings)

    return report


def _transit_report(transit, warnings) -> dict:
    block = transit.quality_report()
    if block.get("mode") != "live":
        warnings.append("Transit timetable is FIXTURE data.")
    if block.get("stop_snap_failures"):
        warnings.append(
            f"{block['stop_snap_failures']} transit stop(s) could not be attached to the walking network.")
    if block.get("poor_stop_snaps"):
        warnings.append(
            f"{block['poor_stop_snaps']} transit stop(s) snap further than "
            f"{POOR_SERVICE_SNAP_M:.0f} m to the nearest street.")
    if block.get("malformed_records"):
        warnings.append(f"{block['malformed_records']} malformed GTFS record(s) were skipped.")
    if not block.get("serves_today"):
        warnings.append(
            "The prepared timetable does not cover today's date; transit answers will use "
            "a date inside its calendar.")
    return block


def _service_report(index, warnings) -> dict:
    categories = {}
    networks = tuple(getattr(index, "networks", ("walk",)))
    for category in index.categories:
        items = index.by_category[category]
        distances = np.array(
            [s.access_distance_m for s in items if s.access_distance_m is not None], dtype=float
        )
        duplicates = duplicate_candidates(items)
        missing_name = sum(1 for s in items if not s.name)
        poor = sum(1 for s in items if s.access_quality == "poor")
        failed = sum(1 for s in items if s.access_quality in {"unreachable", "unsnapped"})
        categories[category.value] = {
            "label": category_label(category),
            "count": len(items),
            "sources": sorted({s.source for s in items}),
            "datasets": sorted({s.source_dataset for s in items}),
            "missing_name": missing_name,
            "missing_name_ratio": round(missing_name / len(items), 3) if items else None,
            "routable": sum(1 for s in items if s.is_routable),
            "poor_snaps": poor,
            "failed_snaps": failed,
            "snap_distance_m": {
                "median": round(float(np.median(distances)), 1) if distances.size else None,
                "p95": round(float(np.percentile(distances, 95)), 1) if distances.size else None,
                "max": round(float(distances.max()), 1) if distances.size else None,
            },
            "duplicate_candidates": len(duplicates),
            "duplicate_samples": duplicates[:MAX_DUPLICATE_SAMPLES],
            "by_network": {
                name: _snap_stats([s.access_for(name) for s in items]) for name in networks
            },
        }
        for name in networks:
            if name == "walk":
                continue
            failures = sum(1 for s in items if not s.is_routable_on(name))
            if failures:
                warnings.append(
                    f"{failures} '{category.value}' location(s) could not be attached to the "
                    f"{name} network."
                )
        if failed:
            warnings.append(
                f"{failed} '{category.value}' location(s) could not be attached to the walking network."
            )
        if poor:
            warnings.append(
                f"{poor} '{category.value}' location(s) snap further than {POOR_SERVICE_SNAP_M:.0f} m "
                "to the nearest street."
            )
        if duplicates:
            warnings.append(
                f"{len(duplicates)} possible duplicate '{category.value}' pair(s) within 25 m."
            )
        if missing_name and category is not ServiceCategory.PARK:
            warnings.append(f"{missing_name} '{category.value}' location(s) have no upstream name.")

    if index.mode != "live":
        warnings.append("Service data is FIXTURE data.")
    for category, errors in (index.source_errors or {}).items():
        for error in errors:
            warnings.append(f"Service source failure for '{category}': {error}")

    return {
        "mode": index.mode,
        "generated_at": index.generated_at,
        "fallback_reason": index.fallback_reason,
        "total": len(index.services),
        "networks": list(networks),
        "routable_by_network": {
            name: sum(1 for s in index.services if s.is_routable_on(name)) for name in networks
        },
        "categories": categories,
    }


def _snap_stats(accesses) -> dict:
    distances = np.array([a.distance_m for a in accesses if a.distance_m is not None], dtype=float)
    return {
        "routable": sum(1 for a in accesses if a.is_routable),
        "poor_snaps": sum(1 for a in accesses if a.quality == "poor"),
        "failed_snaps": sum(1 for a in accesses if a.quality in {"unreachable", "unsnapped"}),
        "median_snap_m": round(float(np.median(distances)), 1) if distances.size else None,
        "max_snap_m": round(float(distances.max()), 1) if distances.size else None,
    }


def write_report(report: dict, path=None) -> Path:
    path = Path(path or DATA_QUALITY_REPORT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_report(path=None) -> Optional[dict]:
    path = Path(path or DATA_QUALITY_REPORT)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def concise(report: Optional[dict]) -> dict:
    """The short form for /data/status and /health."""
    if not report:
        return {"available": False,
                "hint": "Run `python -m app.prepare_data` to generate data/processed/data_quality.json"}
    services = report.get("services", {})
    return {
        "available": True,
        "generated_at": report.get("generated_at"),
        "network": {k: report.get("network", {}).get(k) for k in ("mode", "source", "nodes", "edges")},
        "networks": {
            name: {k: block.get(k) for k in ("mode", "source", "nodes", "edges", "total_length_km")}
            for name, block in (report.get("networks") or {}).items()
        },
        "transit": report.get("transit"),
        "entities": {k: report.get("entities", {}).get(k) for k in ("mode", "source", "counts")},
        "services": {
            "mode": services.get("mode"),
            "total": services.get("total"),
            "by_category": {
                name: {
                    "count": row["count"],
                    "sources": row["sources"],
                    "missing_name": row["missing_name"],
                    "poor_snaps": row["poor_snaps"],
                    "failed_snaps": row["failed_snaps"],
                    "duplicate_candidates": row["duplicate_candidates"],
                }
                for name, row in services.get("categories", {}).items()
            },
        },
        "warning_count": len(report.get("warnings", [])),
        "warnings": report.get("warnings", [])[:20],
    }
