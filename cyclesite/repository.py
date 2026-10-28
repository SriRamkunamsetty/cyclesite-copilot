"""Data model and the storage port (BigQuery in production, synthetic data offline)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import numpy as np

from .backtest import StationSet
from .scoring import ALL_FEATURES


@dataclass(frozen=True)
class PointTable:
    """Locations with infrastructure/POI features; stations also carry observed trips."""

    ids: np.ndarray
    kind: np.ndarray  # "station" or "cell"
    lat: np.ndarray
    lon: np.ndarray
    features: dict[str, np.ndarray]
    trips_low: np.ndarray
    trips_mid: np.ndarray
    trips_high: np.ndarray

    def __post_init__(self) -> None:
        n = self.ids.size
        for name in ALL_FEATURES:
            if name not in self.features or np.asarray(self.features[name]).size != n:
                raise ValueError(f"Missing or mis-sized feature column: {name}")

    def mask(self, kind: str) -> np.ndarray:
        return self.kind == kind

    def take(self, m: np.ndarray) -> PointTable:
        return PointTable(
            self.ids[m],
            self.kind[m],
            self.lat[m],
            self.lon[m],
            {k: np.asarray(v)[m] for k, v in self.features.items()},
            self.trips_low[m],
            self.trips_mid[m],
            self.trips_high[m],
        )


@dataclass(frozen=True)
class CityData:
    city_id: str
    name: str
    source: str  # "bigquery" or "synthetic"
    newest_data: datetime | None
    points: PointTable
    existing_lat: np.ndarray = field(default_factory=lambda: np.array([]))
    existing_lon: np.ndarray = field(default_factory=lambda: np.array([]))
    notes: tuple[str, ...] = ()

    def stations(self) -> StationSet | None:
        m = self.points.mask("station") & np.isfinite(self.points.trips_mid)
        if m.sum() == 0:
            return None
        t = self.points.take(m)
        return StationSet(t.features, t.trips_mid, self.city_id)

    @property
    def has_observed_demand(self) -> bool:
        return self.stations() is not None


class CityRepository(Protocol):
    def list_cities(self) -> list[dict]: ...

    def load_city(self, city_id: str) -> CityData: ...


class CachingRepository:
    """Wraps a repository with a short in-process cache so one chat does not re-query storage."""

    def __init__(self, inner: CityRepository, ttl_seconds: float = 600.0):
        import time

        self._inner = inner
        self._ttl = ttl_seconds
        self._clock = time.monotonic
        self._cache: dict[str, tuple[float, CityData]] = {}

    def list_cities(self) -> list[dict]:
        return self._inner.list_cities()

    def load_city(self, city_id: str) -> CityData:
        hit = self._cache.get(city_id)
        if hit and self._clock() - hit[0] < self._ttl:
            return hit[1]
        data = self._inner.load_city(city_id)
        self._cache[city_id] = (self._clock(), data)
        return data
