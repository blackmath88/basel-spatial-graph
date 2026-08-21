"""The frozen snapshot: the prepared artefacts this repository actually ships.

`data/processed/` is committed, so a fresh clone can run the server without a
network connection and without a preparation step. That is convenient and it is
also a claim that has to be kept honest: the committed files are **real Basel
data frozen at one moment**, not live data.

This module is the bookkeeping for that claim.

    SNAPSHOT.json   a committed manifest: every artefact's size, SHA-256 and
                    whatever generation / retrieval / reference date it carries
    frozen          the file on disk is byte-identical to the manifest entry —
                    you are running exactly what the repository ships
    local           the file differs, or the manifest does not know it — you
                    prepared it yourself with `python -m app.prepare_data`
    fixture         the subsystem fell back to synthetic data; not Basel at all

Writing the manifest is a deliberate act (`python -m app.snapshot --write`),
never a side effect of preparing data. If preparing rewrote it, freshly
downloaded data would immediately relabel itself as "the frozen snapshot", and
the distinction this module exists to draw would be worthless.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from .config import (
    BIKE_NETWORK_CACHE,
    DATA_QUALITY_REPORT,
    ENTITY_CACHE,
    POPULATION_CACHE,
    PROCESSED_DIR,
    ROOT,
    SERVICE_CACHE,
    SNAPSHOT_MANIFEST,
    SPATIAL_GRAPH_CACHE,
    TRANSIT_CACHE,
    WALK_NETWORK_CACHE,
)

FORMAT_VERSION = 1

FROZEN = "frozen"
LOCAL = "local"
FIXTURE = "fixture"
ABSENT = "absent"

STATE_LABELS = {
    FROZEN: "frozen snapshot",
    LOCAL: "prepared locally",
    FIXTURE: "synthetic fixture",
    ABSENT: "not prepared",
}
STATE_EXPLANATIONS = {
    FROZEN: ("The frozen snapshot committed to this repository: real Basel data "
             "prepared once and shipped as files. Real, but not current."),
    LOCAL: ("Prepared on this machine since the snapshot was frozen, and no "
            "longer identical to it. Real data, and newer than what the "
            "repository ships."),
    FIXTURE: ("Synthetic fixture data. Deterministic and offline, but not Basel — "
              "no figure derived from it describes the real city."),
    ABSENT: "No prepared artefact and no fixture fallback recorded.",
}

# Every artefact the running server or an offline rebuild reads. Raw download
# caches (`data/raw/gtfs`, `data/raw/osmnx_cache`, the raw API responses) are
# deliberately absent: they are inputs to preparation, not runtime dependencies.
ARTIFACTS: Dict[str, dict] = {
    "entities": {
        "path": ENTITY_CACHE,
        "consumed_by": "app.ingest → app.graph (the entity graph, the map, /analysis/*)",
        "runtime": True,
    },
    "walk": {
        "path": WALK_NETWORK_CACHE,
        "consumed_by": "app.street_sources → walking routing, transit walk legs, snapping",
        "runtime": True,
    },
    "bike": {
        "path": BIKE_NETWORK_CACHE,
        "consumed_by": "app.street_sources → cycling routing",
        "runtime": True,
    },
    "services": {
        "path": SERVICE_CACHE,
        "consumed_by": "app.service_sources → every accessibility answer, /services",
        "runtime": True,
    },
    "transit": {
        "path": TRANSIT_CACHE,
        "consumed_by": "app.transit_sources → RAPTOR, /transit/*",
        "runtime": True,
    },
    "spatial_graph": {
        "path": SPATIAL_GRAPH_CACHE,
        "consumed_by": "app.spatial_graph → /spatial-graph/*, the standing questions, MCP",
        "runtime": True,
    },
    "data_quality": {
        "path": DATA_QUALITY_REPORT,
        "consumed_by": "app.data_quality → /data/status, provenance caveats",
        "runtime": True,
    },
    "population": {
        "path": POPULATION_CACHE,
        "consumed_by": "app.prepare_spatial_graph (build time only; the figures "
                       "themselves live inside the spatial graph)",
        "runtime": False,
    },
}


def relative(path) -> str:
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def digest(path) -> Optional[dict]:
    """Size and SHA-256 of one artefact, or None if it is not there."""
    path = Path(path)
    if not path.exists():
        return None
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return {"bytes": path.stat().st_size, "sha256": sha.hexdigest()}


def read_manifest(path=None) -> Optional[dict]:
    path = Path(path or SNAPSHOT_MANIFEST)
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if manifest.get("format_version") != FORMAT_VERSION:
        return None
    manifest["manifest_path"] = relative(path)
    return manifest


class RuntimeSnapshot:
    """Which of the three data states each subsystem is actually running on."""

    def __init__(self, manifest: Optional[dict] = None,
                 states: Optional[Dict[str, str]] = None):
        self.manifest = manifest or {}
        self.states = states or {}

    @classmethod
    def load(cls, path=None) -> "RuntimeSnapshot":
        manifest = read_manifest(path)
        artifacts = (manifest or {}).get("artifacts", {})
        states = {}
        for key, spec in ARTIFACTS.items():
            actual = digest(spec["path"])
            expected = artifacts.get(key)
            if actual is None:
                states[key] = ABSENT
            elif (expected and actual["sha256"] == expected.get("sha256")
                    and actual["bytes"] == expected.get("bytes")):
                states[key] = FROZEN
            else:
                states[key] = LOCAL
        return cls(manifest, states)

    @property
    def available(self) -> bool:
        return bool(self.manifest)

    def state(self, key: str, mode: Optional[str] = None) -> str:
        """The data state of one subsystem.

        `mode` is the subsystem's own live/fixture verdict, which wins: a
        server that fell back to the fixture is not running the snapshot,
        whatever is lying on disk.
        """
        if mode == "fixture":
            return FIXTURE
        return self.states.get(key, ABSENT)

    def entry(self, key: str) -> dict:
        return (self.manifest.get("artifacts", {}) or {}).get(key, {})

    def block(self, key: str, mode: Optional[str] = None) -> dict:
        """The `data_state` block embedded in a status response."""
        state = self.state(key, mode)
        entry = self.entry(key) if state == FROZEN else {}
        return {
            "state": state,
            "label": STATE_LABELS[state],
            "explanation": STATE_EXPLANATIONS[state],
            "frozen": state == FROZEN,
            "snapshot_created_at": self.manifest.get("created_at") if state == FROZEN else None,
            "prepared_at": entry.get("prepared_at"),
            "refresh_command": self.manifest.get("refresh_command", "python -m app.prepare_data"),
        }

    def overall(self) -> str:
        """One word for the whole runtime, biased towards the least reassuring."""
        states = set(self.states.values())
        if FIXTURE in states:
            return FIXTURE
        runtime = {key for key, spec in ARTIFACTS.items() if spec["runtime"]}
        observed = {self.states.get(key, ABSENT) for key in runtime}
        if observed == {FROZEN}:
            return FROZEN
        if FROZEN in observed:
            return "mixed"
        return LOCAL if LOCAL in observed else ABSENT

    def describe(self, modes: Optional[Dict[str, str]] = None) -> dict:
        """The snapshot block for /health and /data/status."""
        modes = modes or {}
        overall = self.overall() if not modes else self._overall_with(modes)
        return {
            "available": self.available,
            "state": overall,
            "label": STATE_LABELS.get(overall, overall),
            "is_frozen_snapshot": overall == FROZEN,
            "note": self.manifest.get("note"),
            "created_at": self.manifest.get("created_at"),
            "snapshot_id": self.manifest.get("snapshot_id"),
            "manifest_path": self.manifest.get("manifest_path"),
            "refresh_command": self.manifest.get("refresh_command", "python -m app.prepare_data"),
            "valid_until": self.manifest.get("valid_until"),
            "artifacts": {
                key: {
                    "path": relative(spec["path"]),
                    "state": self.state(key, modes.get(key)),
                    "consumed_by": spec["consumed_by"],
                    "required_at_startup": spec["runtime"],
                    **{k: v for k, v in self.entry(key).items() if k != "sha256"},
                }
                for key, spec in ARTIFACTS.items()
            },
        }

    def _overall_with(self, modes: Dict[str, str]) -> str:
        states = {self.state(key, modes.get(key))
                  for key, spec in ARTIFACTS.items() if spec["runtime"]}
        if states == {FROZEN}:
            return FROZEN
        if states == {FIXTURE}:
            return FIXTURE
        if FROZEN in states or LOCAL in states:
            return "mixed" if len(states) > 1 else states.pop()
        return ABSENT


_RUNTIME: Optional[RuntimeSnapshot] = None


def runtime_snapshot() -> RuntimeSnapshot:
    """Process-wide snapshot state, resolved once."""
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = RuntimeSnapshot.load()
    return _RUNTIME


# --- writing the manifest ----------------------------------------------------
# Everything below runs only from the command line. The imports stay local so
# that the runtime path above costs one JSON read and eight hashes.

def _mtime(path) -> str:
    stamp = datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc)
    return stamp.isoformat(timespec="seconds")


def _stamp(path, value) -> dict:
    """Prefer a date the artefact states about itself; fall back to its mtime.

    The fallback is resolved when the manifest is written and then travels in
    the committed manifest, because a clone's file timestamps are meaningless.
    """
    if value:
        return {"prepared_at": value, "prepared_at_method": "recorded in the artefact"}
    return {"prepared_at": _mtime(path),
            "prepared_at_method": "file modification time when the snapshot was frozen"}


def _iso_date(value) -> Optional[str]:
    """GTFS writes service dates as `20261212`; a manifest should not."""
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text or None


def _describe_entities(path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "contents": "Normalized Basel areas, schools and accidents",
        "data": "real Basel data (observed)",
        "mode": payload.get("mode"),
        "source": payload.get("source"),
        "source_url": "https://data.bs.ch/",
        "license": "Open Government Data Basel-Stadt (CC BY 3.0 CH)",
        "counts": {kind: len(payload.get(kind, []))
                   for kind in ("areas", "schools", "accidents")},
        **_stamp(path, payload.get("generated_at")),
    }


def _describe_network(path, kind) -> dict:
    from .street_sources import load_network

    network = load_network(kind)
    stats = network.stats()
    provenance = network.provenance
    return {
        "contents": f"Normalized OpenStreetMap {'cycling' if kind == 'bike' else 'walking'} network",
        "data": "real Basel data (observed)",
        "mode": stats["mode"],
        "source": stats["source"],
        "source_url": "https://www.openstreetmap.org/",
        "license": provenance.get("license") or "ODbL 1.0",
        "place": provenance.get("place"),
        "nodes": stats["nodes"],
        "edges": stats["edges"],
        "total_length_km": round(stats["total_length_m"] / 1000, 1),
        "retrieved_at": provenance.get("retrieved_at"),
        **_stamp(path, provenance.get("retrieved_at")),
    }


def _describe_services(path) -> dict:
    from .service_sources import read_cache

    payload = read_cache(path)
    services = payload["services"]
    categories: Dict[str, int] = {}
    retrieved = set()
    for service in services:
        categories[service.category.value] = categories.get(service.category.value, 0) + 1
        if service.provenance.get("retrieved_at"):
            retrieved.add(service.provenance["retrieved_at"])
    return {
        "contents": "Normalized service locations with a walk and a bike access node each",
        "data": "real Basel data (observed)",
        "mode": "live",
        "source": "Open Government Data Basel-Stadt (data.bs.ch) + OpenStreetMap",
        "license": "CC BY 3.0 CH (data.bs.ch) / ODbL 1.0 (OpenStreetMap)",
        "total": len(services),
        "by_category": dict(sorted(categories.items())),
        "network_fingerprints": payload.get("network_fingerprints"),
        "retrieved_at": max(retrieved) if retrieved else None,
        **_stamp(path, payload.get("generated_at")),
    }


def _describe_transit(path) -> dict:
    from .transit_sources.cache import read_cache

    timetable = read_cache(path)
    window = timetable.service_window()
    provenance = timetable.meta or {}
    return {
        "contents": "The Basel subset of the Swiss national timetable",
        "data": "real Basel data (observed)",
        "mode": "live",
        "source": provenance.get("source"),
        "source_url": "https://data.opentransportdata.swiss/",
        "license": provenance.get("license"),
        "feed": provenance.get("feed"),
        "feed_version": provenance.get("feed_version"),
        "stops": timetable.stop_count,
        "routes": timetable.route_count,
        "trips": timetable.trip_count,
        "service_dates": {"first": window[0], "last": window[1]},
        "extraction": provenance.get("extraction"),
        "retrieved_at": provenance.get("retrieved_at"),
        **_stamp(path, provenance.get("retrieved_at")),
    }


def _describe_population(path) -> dict:
    from .population import read_cache

    payload = read_cache(path)
    provenance = payload.get("provenance", {})
    years = payload.get("years", [])
    return {
        "contents": "Neighbourhood population by age group",
        "data": "official statistics",
        "mode": payload.get("mode"),
        "source": provenance.get("source"),
        "dataset": provenance.get("dataset"),
        "dataset_title": provenance.get("dataset_title"),
        "source_url": provenance.get("source_url"),
        "license": provenance.get("license"),
        "reference_years": [min(years), max(years)] if years else [],
        "latest_reference_year": payload.get("latest_year"),
        "observations": len(payload.get("observations", [])),
        "retrieved_at": provenance.get("retrieved_at"),
        **_stamp(path, provenance.get("retrieved_at")),
    }


def _describe_spatial_graph(path) -> dict:
    from .spatial_graph.model import NetworkXSpatialGraph

    graph = NetworkXSpatialGraph.load(path)
    metadata = graph.metadata
    return {
        "contents": "The heterogeneous typed graph of Basel",
        "data": "derived from the artefacts above",
        "mode": metadata.get("mode"),
        "nodes": graph.graph.number_of_nodes(),
        "edges": graph.graph.number_of_edges(),
        "node_types": graph.node_counts(),
        "population_reference_year": metadata.get("population_reference_year"),
        "rebuild_command": "python -m app.prepare_spatial_graph",
        **_stamp(path, metadata.get("generated_at")),
    }


def _describe_quality(path) -> dict:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "contents": "Generated data-quality report",
        "data": "derived from the artefacts above",
        "warnings": len(report.get("warnings", [])),
        **_stamp(path, report.get("generated_at")),
    }


_DESCRIBERS = {
    "entities": _describe_entities,
    "walk": lambda path: _describe_network(path, "walk"),
    "bike": lambda path: _describe_network(path, "bike"),
    "services": _describe_services,
    "transit": _describe_transit,
    "spatial_graph": _describe_spatial_graph,
    "data_quality": _describe_quality,
    "population": _describe_population,
}

NOTE = (
    "A frozen snapshot of real Basel data, prepared once and committed so the "
    "server runs straight after `git clone` with no downloads. It is real, and "
    "it is not current: nothing here is refreshed until you run the refresh "
    "command yourself."
)


def build_manifest() -> dict:
    """Describe whatever prepared artefacts are on disk right now."""
    artifacts: Dict[str, dict] = {}
    for key, spec in ARTIFACTS.items():
        path = Path(spec["path"])
        fingerprint = digest(path)
        if fingerprint is None:
            continue
        try:
            described = _DESCRIBERS[key](path)
        except Exception as exc:                      # never freeze what we cannot read
            described = {"unreadable": str(exc)}
        artifacts[key] = {
            "path": relative(path),
            "required_at_startup": spec["runtime"],
            "consumed_by": spec["consumed_by"],
            **fingerprint,
            **described,
        }
    transit = artifacts.get("transit", {})
    valid_until = _iso_date((transit.get("service_dates") or {}).get("last"))
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "format_version": FORMAT_VERSION,
        "kind": "frozen_snapshot",
        "frozen": True,
        "snapshot_id": created[:10],
        "created_at": created,
        "note": NOTE,
        "refresh_command": "python -m app.prepare_data",
        "refreeze_command": "python -m app.snapshot --write",
        "coverage": "Canton of Basel-Stadt (Basel, Riehen, Bettingen); "
                    "the timetable extends into Baselland, Baden-Wurttemberg and Alsace",
        "valid_until": valid_until,
        "valid_until_note": (
            "The last service date in the frozen timetable. Transit answers for "
            "later dates fall back to that day's pattern only if the feed still "
            "covers it; refresh the snapshot to move the window."
            if valid_until else None),
        "artifacts": artifacts,
    }


def write_manifest(path=None) -> dict:
    manifest = build_manifest()
    path = Path(path or SNAPSHOT_MANIFEST)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return manifest


def check(path=None) -> dict:
    """Compare what is on disk with the committed manifest."""
    manifest = read_manifest(path)
    rows = {}
    for key, spec in ARTIFACTS.items():
        actual = digest(spec["path"])
        expected = (manifest or {}).get("artifacts", {}).get(key)
        if actual is None:
            verdict = "missing"
        elif expected is None:
            verdict = "not in the snapshot"
        elif (actual["sha256"] == expected.get("sha256")
                and actual["bytes"] == expected.get("bytes")):
            verdict = "matches"
        else:
            verdict = "differs"
        rows[key] = {"path": relative(spec["path"]), "verdict": verdict,
                     "bytes": (actual or {}).get("bytes")}
    return {"manifest": manifest, "artifacts": rows,
            "matches": all(row["verdict"] == "matches" for row in rows.values())}


def print_check(result: Optional[dict] = None) -> bool:
    result = result or check()
    manifest = result["manifest"]
    print("Snapshot\n")
    if not manifest:
        print(f"  no committed manifest at {relative(SNAPSHOT_MANIFEST)}")
        print("  Everything prepared here is local data, not a frozen snapshot.")
        return False
    print(f"  manifest: {manifest.get('manifest_path')} "
          f"(frozen {manifest.get('created_at')})")
    for key, row in result["artifacts"].items():
        size = f"{row['bytes'] / 1_048_576:.1f} MB" if row["bytes"] else "—"
        print(f"  {key:<14} {size:>8}  {row['verdict']}")
    if result["matches"]:
        print("\n  Everything on disk is the committed frozen snapshot.")
    else:
        print("\n  Some artefacts differ from the committed snapshot; the server "
              "will report them as `local`.")
        print(f"  To publish them as the new frozen snapshot: "
              f"{manifest.get('refreeze_command', 'python -m app.snapshot --write')}")
    return result["matches"]


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Inspect or re-freeze the committed data snapshot.")
    parser.add_argument("--write", action="store_true",
                        help="rewrite SNAPSHOT.json from the artefacts on disk")
    args = parser.parse_args(argv)

    if args.write:
        manifest = write_manifest()
        print(f"Wrote {relative(SNAPSHOT_MANIFEST)}\n")
        total = sum(entry.get("bytes", 0) for entry in manifest["artifacts"].values())
        for key, entry in manifest["artifacts"].items():
            print(f"  {key:<14} {entry['bytes'] / 1_048_576:>6.1f} MB  "
                  f"{entry.get('prepared_at', '—')}")
        print(f"\n  {len(manifest['artifacts'])} artefacts, {total / 1_048_576:.1f} MB")
        print(f"  valid_until: {manifest.get('valid_until') or '—'}")
        print("\n  Commit data/processed/ to ship this snapshot.")
        return 0
    return 0 if print_check() else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
