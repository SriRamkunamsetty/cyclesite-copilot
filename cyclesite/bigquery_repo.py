"""BigQuery-backed repository: reads the tables built by ``cyclesite build-city``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np

from .config import Settings, validate_identifier
from .repository import CityData, PointTable
from .scoring import ALL_FEATURES

_LABELS = {"app": "cyclesite-copilot", "component": "repository"}


def _f(v: Any) -> float:
    return float("nan") if v is None else float(v)


class BigQueryRepository:
    def __init__(self, client: Any, settings: Settings):
        if not settings.project_id:
            raise ValueError("GOOGLE_CLOUD_PROJECT must be set for the BigQuery backend.")
        self._client = client
        self._s = settings
        validate_identifier(settings.dataset, "dataset")

    def _table(self, name: str) -> str:
        return f"`{self._s.project_id}.{self._s.dataset}.{validate_identifier(name, 'table')}`"

    def _query(self, sql: str, city_id: str | None = None) -> list[Any]:
        from google.cloud import bigquery

        params = []
        if city_id is not None:
            params.append(bigquery.ScalarQueryParameter("city", "STRING", city_id))
        cfg = bigquery.QueryJobConfig(
            query_parameters=params,
            maximum_bytes_billed=self._s.max_bytes_billed,
            labels=_LABELS,
        )
        return list(self._client.query(sql, job_config=cfg, location=self._s.location).result())

    def list_cities(self) -> list[dict]:
        rows = self._query(
            f"SELECT city_id, name, demand_kind, newest_data, built_at "
            f"FROM {self._table('city_meta')} ORDER BY city_id"
        )
        return [
            {
                "city_id": r["city_id"],
                "name": r["name"],
                "has_observed_demand": r["demand_kind"] == "observed",
                "newest_data": r["newest_data"].isoformat() if r["newest_data"] else None,
                "built_at": r["built_at"].isoformat() if r["built_at"] else None,
                "source": "bigquery",
            }
            for r in rows
        ]

    def load_city(self, city_id: str) -> CityData:
        meta = self._query(
            f"SELECT name, newest_data FROM {self._table('city_meta')} WHERE city_id = @city",
            city_id,
        )
        if not meta:
            raise KeyError(f"City '{city_id}' has not been built. Run: cyclesite build-city")
        cols = ", ".join(ALL_FEATURES)
        rows = self._query(
            f"SELECT point_id, kind, lat, lon, {cols}, trips_low, trips_mid, trips_high "
            f"FROM {self._table('city_points')} WHERE city_id = @city ORDER BY point_id",
            city_id,
        )
        if not rows:
            raise KeyError(f"City '{city_id}' has no points; rebuild it.")
        feats = {n: np.array([_f(r[n]) for r in rows]) for n in ALL_FEATURES}
        feats["lit_share"] = np.nan_to_num(feats["lit_share"], nan=0.0)
        feats["paved_share"] = np.nan_to_num(feats["paved_share"], nan=0.5)
        for n in ALL_FEATURES:
            feats[n] = np.nan_to_num(feats[n], nan=0.0)
        pts = PointTable(
            np.array([str(r["point_id"]) for r in rows]),
            np.array([r["kind"] for r in rows]),
            np.array([_f(r["lat"]) for r in rows]),
            np.array([_f(r["lon"]) for r in rows]),
            feats,
            np.array([_f(r["trips_low"]) for r in rows]),
            np.array([_f(r["trips_mid"]) for r in rows]),
            np.array([_f(r["trips_high"]) for r in rows]),
        )
        shops = self._query(
            f"SELECT lat, lon FROM {self._table('city_existing_shops')} WHERE city_id = @city",
            city_id,
        )
        newest = meta[0]["newest_data"]
        if newest is not None and newest.tzinfo is None:
            newest = newest.replace(tzinfo=UTC)
        return CityData(
            city_id=city_id,
            name=meta[0]["name"],
            source="bigquery",
            newest_data=newest if isinstance(newest, datetime) else None,
            points=pts,
            existing_lat=np.array([_f(r["lat"]) for r in shops]),
            existing_lon=np.array([_f(r["lon"]) for r in shops]),
        )
