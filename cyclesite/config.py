"""Runtime settings, read once from environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def validate_identifier(value: str, what: str = "identifier") -> str:
    """Allow only simple SQL identifiers; used for anything pasted into SQL text."""
    if not isinstance(value, str) or not _IDENT.fullmatch(value):
        raise ValueError(f"Invalid {what}: {value!r}")
    return value


@dataclass(frozen=True)
class Settings:
    backend: str = "bigquery"  # "bigquery" or "synthetic"
    project_id: str | None = None
    dataset: str = "cyclesite"
    location: str = "US"  # public datasets live in US; our dataset must match
    max_bytes_billed: int = 10 * 10**9  # per agent-issued query
    freshness_warn_days: int = 365
    freshness_expired_days: int = 3 * 365
    model: str = "gemini-3.6-flash"

    @classmethod
    def from_env(cls) -> Settings:
        backend = os.getenv("CYCLESITE_BACKEND", "bigquery").lower()
        if backend not in {"bigquery", "synthetic"}:
            raise ValueError("CYCLESITE_BACKEND must be 'bigquery' or 'synthetic'")
        return cls(
            backend=backend,
            project_id=os.getenv("GOOGLE_CLOUD_PROJECT") or None,
            dataset=validate_identifier(os.getenv("CYCLESITE_DATASET", "cyclesite"), "dataset"),
            location=os.getenv("CYCLESITE_BQ_LOCATION", "US"),
            max_bytes_billed=int(os.getenv("CYCLESITE_MAX_BYTES_BILLED", str(10 * 10**9))),
            model=os.getenv("CYCLESITE_MODEL", "gemini-3.6-flash"),
        )
