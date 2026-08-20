"""Contract every service (POI) provider implements.

Providers differ wildly — an Opendatasoft REST API, an Overpass query through
OSMnx, a hand-written fixture — but each one returns the same thing: a list of
`ServiceLocation` for one category, each carrying its own provenance.
"""
from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from typing import Iterable, List, Optional, Sequence

import numpy as np

from ..service_model import ServiceCategory, ServiceLocation

# Two entries of the same category closer than this, with compatible names, are
# reported as possible duplicates in the data-quality report.
DUPLICATE_DISTANCE_M = 25.0


def safe_id(*parts) -> str:
    """URL-safe, stable id. OSM ids like `node/123` would break path routing."""
    joined = ":".join(str(p) for p in parts if p not in (None, ""))
    return re.sub(r"[^A-Za-z0-9:_.-]+", "-", joined)


def normalize_name(name) -> Optional[str]:
    """Trim and collapse whitespace. Never fabricates a name; empty stays None."""
    if name is None:
        return None
    text = str(name).strip()
    if not text or text.lower() in {"nan", "none", "null", "-"}:
        return None
    return re.sub(r"\s+", " ", text)


def _name_key(name: Optional[str]) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name.lower())
    return re.sub(r"[^a-z0-9]+", "", text)


def duplicate_candidates(services: Sequence[ServiceLocation], distance_m: float = DUPLICATE_DISTANCE_M):
    """Same category, nearly the same place, and a compatible name.

    Reported, never removed: two pharmacies really can share a building.
    """
    from ..projection import to_metric

    if len(services) < 2:
        return []
    xs, ys = to_metric([s.lon for s in services], [s.lat for s in services])
    xs = np.atleast_1d(np.asarray(xs, dtype=float))
    ys = np.atleast_1d(np.asarray(ys, dtype=float))
    distances = np.hypot(xs[:, None] - xs[None, :], ys[:, None] - ys[None, :])
    rows, cols = np.nonzero(np.triu(distances <= distance_m, k=1))
    pairs = []
    for i, j in zip(rows, cols):
        a, b = services[int(i)], services[int(j)]
        key_a, key_b = _name_key(a.name), _name_key(b.name)
        if key_a and key_b and key_a != key_b:
            continue  # different names at the same address are usually genuine
        pairs.append({
            "category": a.category.value,
            "ids": [a.id, b.id],
            "names": [a.name, b.name],
            "distance_m": round(float(distances[int(i), int(j)]), 1),
        })
    return pairs


class ServiceSource(ABC):
    """Supplies service locations for one or more categories."""

    name: str = "unknown"
    license: Optional[str] = None
    source_url: Optional[str] = None

    @property
    @abstractmethod
    def categories(self) -> Sequence[ServiceCategory]:
        """Which categories this source can provide."""

    @abstractmethod
    def fetch(self, category: ServiceCategory) -> List[ServiceLocation]:
        """Return locations for one category, or raise `ServiceSourceError`."""


def dedupe(services: Iterable[ServiceLocation]) -> List[ServiceLocation]:
    """Drop exact id collisions, keeping the first occurrence."""
    seen, result = set(), []
    for service in services:
        if service.id in seen:
            continue
        seen.add(service.id)
        result.append(service)
    return result
