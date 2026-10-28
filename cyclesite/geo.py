"""Small geographic helpers (no GIS dependency)."""

from __future__ import annotations

import numpy as np

EARTH_RADIUS_M = 6_371_008.8


def haversine_matrix(
    lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray, block: int = 512
) -> np.ndarray:
    """Great-circle distances in metres, shape (len(lat1), len(lat2)), float32."""
    lat1 = np.radians(np.asarray(lat1, dtype=np.float64))
    lon1 = np.radians(np.asarray(lon1, dtype=np.float64))
    lat2 = np.radians(np.asarray(lat2, dtype=np.float64))
    lon2 = np.radians(np.asarray(lon2, dtype=np.float64))
    out = np.empty((lat1.size, lat2.size), dtype=np.float32)
    for start in range(0, lat1.size, block):
        stop = min(start + block, lat1.size)
        dlat = lat2[None, :] - lat1[start:stop, None]
        dlon = lon2[None, :] - lon1[start:stop, None]
        a = (
            np.sin(dlat / 2) ** 2
            + np.cos(lat1[start:stop, None]) * np.cos(lat2[None, :]) * np.sin(dlon / 2) ** 2
        )
        out[start:stop] = (2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))).astype(
            np.float32
        )
    return out
