"""Coordinate transforms between WGS84 and the Swiss metric CRS.

All distance maths in this project happens in `METRIC_CRS` (EPSG:2056,
CH1903+/LV95) so that lengths, buffers and nearest-node searches are in real
metres rather than degrees.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Sequence, Tuple

from .config import GEOGRAPHIC_CRS, METRIC_CRS


@lru_cache(maxsize=4)
def _transformer(src: str, dst: str):
    from pyproj import Transformer  # imported lazily: keeps import cost off cold paths

    return Transformer.from_crs(src, dst, always_xy=True)


def validate_lonlat(lon: float, lat: float) -> Tuple[float, float]:
    """Return floats or raise ValueError for NaN / out-of-range input."""
    try:
        lon_f, lat_f = float(lon), float(lat)
    except (TypeError, ValueError):
        raise ValueError("Coordinates must be numbers")
    if not (lon_f == lon_f and lat_f == lat_f):  # NaN check without importing math
        raise ValueError("Coordinates must not be NaN")
    if not (-180.0 <= lon_f <= 180.0):
        raise ValueError(f"Longitude {lon_f} is outside [-180, 180]")
    if not (-90.0 <= lat_f <= 90.0):
        raise ValueError(f"Latitude {lat_f} is outside [-90, 90]")
    return lon_f, lat_f


def to_metric(lon, lat):
    """Project lon/lat (scalars or sequences) to METRIC_CRS x/y in metres."""
    return _transformer(GEOGRAPHIC_CRS, METRIC_CRS).transform(lon, lat)


def to_wgs84(x, y):
    """Unproject METRIC_CRS x/y (scalars or sequences) back to lon/lat."""
    return _transformer(METRIC_CRS, GEOGRAPHIC_CRS).transform(x, y)


def project_geometry(geom, inverse: bool = False):
    """Project a shapely geometry between WGS84 and METRIC_CRS."""
    from shapely.ops import transform as shapely_transform

    src, dst = (METRIC_CRS, GEOGRAPHIC_CRS) if inverse else (GEOGRAPHIC_CRS, METRIC_CRS)
    return shapely_transform(_transformer(src, dst).transform, geom)


def metric_coords(points: Sequence[Tuple[float, float]]):
    """Project a sequence of (lon, lat) pairs to a list of (x, y) pairs."""
    if not points:
        return []
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    xs, ys = to_metric(lons, lats)
    return list(zip(xs, ys))
