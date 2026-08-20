"""The prepared service cache: `data/processed/basel_services.json`.

Holds every normalized `ServiceLocation` *including* its walking-network access
node, so the server never re-snaps on startup unless the network changed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from ..config import SERVICE_CACHE
from ..errors import ServiceSourceError
from ..service_model import ServiceLocation


def network_fingerprint(streets) -> str:
    """Changes whenever a network changes, invalidating that network's snaps."""
    return "{}:{}:{}".format(
        streets.graph.number_of_nodes(),
        streets.graph.number_of_edges(),
        streets.provenance.get("retrieved_at") or streets.mode,
    )


def network_fingerprints(networks: dict) -> dict:
    """{network name: fingerprint} for every prepared street network."""
    return {name: network_fingerprint(streets) for name, streets in networks.items()}


def write_cache(services: Iterable[ServiceLocation], fingerprints, path=None,
                errors: Optional[dict] = None) -> Path:
    if isinstance(fingerprints, str):  # a single walking fingerprint
        fingerprints = {"walk": fingerprints}
    path = Path(path or SERVICE_CACHE)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "network_fingerprints": dict(fingerprints),
        # Kept so a V0.3 reader still finds the walking fingerprint it expects.
        "network_fingerprint": fingerprints.get("walk"),
        "errors": errors or {},
        "services": [service.to_dict() for service in services],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def read_cache(path=None) -> dict:
    path = Path(path or SERVICE_CACHE)
    if not path.exists():
        raise ServiceSourceError(
            f"No prepared service cache at {path}. Run `python -m app.prepare_data`."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        services: List[ServiceLocation] = [
            ServiceLocation.from_dict(row) for row in payload["services"]
        ]
    except Exception as exc:
        raise ServiceSourceError(f"Service cache at {path} is unreadable: {exc}")
    if not services:
        raise ServiceSourceError(f"Service cache at {path} contains no services")
    return {
        "services": services,
        "network_fingerprint": payload.get("network_fingerprint"),
        "network_fingerprints": payload.get("network_fingerprints") or {},
        "generated_at": payload.get("generated_at"),
        "errors": payload.get("errors", {}),
        "cache_path": str(path),
    }
