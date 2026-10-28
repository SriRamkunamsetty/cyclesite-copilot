"""Small builders shared by tests."""

from __future__ import annotations

import numpy as np

from cyclesite.optimizer import Candidates, DemandPoints, Existing


def demand(lat, lon, mid, ride=None, spread=0.0) -> DemandPoints:
    lat, lon, mid = (np.asarray(x, dtype=float) for x in (lat, lon, mid))
    ride = np.ones_like(mid) if ride is None else np.asarray(ride, dtype=float)
    ids = np.array([f"D{i}" for i in range(mid.size)])
    return DemandPoints(ids, lat, lon, mid * (1 - spread), mid, mid * (1 + spread), ride)


def cands(lat, lon, ride=None) -> Candidates:
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    ride = np.ones_like(lat) if ride is None else np.asarray(ride, dtype=float)
    return Candidates(np.array([f"C{i}" for i in range(lat.size)]), lat, lon, ride)


def existing(lat, lon) -> Existing:
    return Existing(np.asarray(lat, dtype=float), np.asarray(lon, dtype=float))


class FakeState(dict):
    """Behaves like ADK session state for tool tests."""


class FakeContext:
    def __init__(self) -> None:
        self.state = FakeState()
