"""Wiring: settings, repository and (optionally) BigQuery client, created lazily."""

from __future__ import annotations

from functools import lru_cache

from cyclesite.config import Settings
from cyclesite.repository import CachingRepository, CityRepository


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache(maxsize=1)
def get_bq_client():
    from google.cloud import bigquery

    s = get_settings()
    return bigquery.Client(project=s.project_id, location=s.location)


@lru_cache(maxsize=1)
def get_repository() -> CityRepository:
    s = get_settings()
    if s.backend == "synthetic":
        from cyclesite.synthetic import SyntheticRepository

        return CachingRepository(SyntheticRepository())
    from cyclesite.bigquery_repo import BigQueryRepository

    return CachingRepository(BigQueryRepository(get_bq_client(), s))


def reset_caches() -> None:
    """Used by tests after changing environment variables."""
    for fn in (get_settings, get_bq_client, get_repository, get_guard):
        fn.cache_clear()


class _LazyEstimator:
    """Creates the BigQuery client only when the first query needs a dry run."""

    def estimate_bytes(self, sql: str) -> int:
        from cyclesite.guardrails import BigQueryEstimator

        return BigQueryEstimator(get_bq_client(), get_settings().location).estimate_bytes(sql)


@lru_cache(maxsize=1)
def get_guard():
    from cyclesite.guardrails import SqlGuard

    s = get_settings()
    return SqlGuard(_LazyEstimator(), s.max_bytes_billed, s.project_id)
