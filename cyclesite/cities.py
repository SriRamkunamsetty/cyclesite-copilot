"""City definitions for the build pipeline, loaded from ``cities.yaml`` and validated.

Names that end up inside SQL text (tables, columns) must match strict patterns, so a typo or a
hostile config file cannot smuggle in extra SQL. Values (coordinates, ids) always travel as
BigQuery query parameters instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .config import validate_identifier

_CITY_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
_TABLE_PATH = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")


def validate_table_path(value: str) -> str:
    if not isinstance(value, str) or not _TABLE_PATH.fullmatch(value):
        raise ValueError(f"Invalid table path (expected project.dataset.table): {value!r}")
    return value


@dataclass(frozen=True)
class BikeshareSource:
    trips_table: str
    time_column: str
    start_id: str
    start_lat: str
    start_lon: str
    end_id: str
    end_lat: str
    end_lon: str

    def __post_init__(self) -> None:
        validate_table_path(self.trips_table)
        for name in (
            "time_column",
            "start_id",
            "start_lat",
            "start_lon",
            "end_id",
            "end_lat",
            "end_lon",
        ):
            validate_identifier(getattr(self, name), name)


@dataclass(frozen=True)
class CitySpec:
    city_id: str
    name: str
    south: float
    west: float
    north: float
    east: float
    grid_spacing_m: float = 500.0
    feature_radius_m: float = 600.0
    window_months: int = 12
    bikeshare: BikeshareSource | None = None

    def __post_init__(self) -> None:
        if not _CITY_ID.fullmatch(self.city_id):
            raise ValueError(f"Invalid city id {self.city_id!r}: use lowercase letters/digits/_")
        if not (-90 <= self.south < self.north <= 90 and -180 <= self.west < self.east <= 180):
            raise ValueError(f"{self.city_id}: bounding box is empty or out of range")
        if (self.north - self.south) > 2.0 or (self.east - self.west) > 2.0:
            raise ValueError(f"{self.city_id}: bounding box larger than 2 degrees; split it")
        if not 100 <= self.grid_spacing_m <= 5000:
            raise ValueError("grid_spacing_m must be between 100 and 5000")
        if not 100 <= self.feature_radius_m <= 3000:
            raise ValueError("feature_radius_m must be between 100 and 3000")
        if not 3 <= self.window_months <= 36:
            raise ValueError("window_months must be between 3 and 36")

    @property
    def demand_kind(self) -> str:
        return "observed" if self.bikeshare else "modelled"


def _parse(city_id: str, raw: dict[str, Any]) -> CitySpec:
    bbox = raw["bbox"]
    bs = raw.get("bikeshare")
    return CitySpec(
        city_id=city_id,
        name=str(raw["name"]),
        south=float(bbox["south"]),
        west=float(bbox["west"]),
        north=float(bbox["north"]),
        east=float(bbox["east"]),
        grid_spacing_m=float(raw.get("grid_spacing_m", 500)),
        feature_radius_m=float(raw.get("feature_radius_m", 600)),
        window_months=int(raw.get("window_months", 12)),
        bikeshare=BikeshareSource(**bs) if bs else None,
    )


def load_cities(path: str | Path | None = None) -> dict[str, CitySpec]:
    if path is None:
        text = resources.files("cyclesite").joinpath("cities.yaml").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    cities = data.get("cities") or {}
    return {cid: _parse(cid, raw) for cid, raw in cities.items()}
