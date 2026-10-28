"""Candidate-site grid over a bounding box."""

from __future__ import annotations

import math

import numpy as np

_M_PER_DEG_LAT = 111_320.0


def make_grid(
    south: float, west: float, north: float, east: float, spacing_m: float
) -> tuple[np.ndarray, np.ndarray]:
    """Cell-centre latitudes/longitudes, spaced ``spacing_m`` apart (metres)."""
    mid_lat = (south + north) / 2.0
    dlat = spacing_m / _M_PER_DEG_LAT
    dlon = spacing_m / (_M_PER_DEG_LAT * math.cos(math.radians(mid_lat)))
    lats = np.arange(south + dlat / 2, north, dlat)
    lons = np.arange(west + dlon / 2, east, dlon)
    lon_g, lat_g = np.meshgrid(lons, lats)
    return lat_g.ravel(), lon_g.ravel()
