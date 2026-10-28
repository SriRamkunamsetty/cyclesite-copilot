"""Prepare one city's tables in BigQuery. A person runs this; the agent never does.

Steps (each is cost-checked with a dry run first, and refuses to run above its byte cap):
  1. Copy the OpenStreetMap features inside the city's bounding box into our own dataset.
  2. For bikeshare cities, read monthly trips per station and turn them into yearly demand with
     a low/high range (see demand.py).
  3. Build the candidate grid, upload stations + grid as a points table.
  4. Measure bike infrastructure and nearby places around every point; replace the city's rows
     in ``city_points`` and ``city_meta`` in one transaction.
  5. Optionally load the existing shops from a CSV (name, lat, lon).
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from .cities import CitySpec
from .config import Settings, validate_identifier
from .demand import build_station_demand
from .grid import make_grid

log = logging.getLogger("cyclesite.build")

OSM_TABLE = "bigquery-public-data.geo_openstreetmap.planet_features"
LABELS = {"app": "cyclesite-copilot", "component": "build"}
ON_DEMAND_USD_PER_TB = 6.25  # list price when written; used only for the estimate text
MAX_SHOPS = 5000


class BuildAborted(RuntimeError):
    """Raised before any money is spent (or when the data cannot support a build)."""


@dataclass
class BuildReport:
    city_id: str
    dry_run: bool
    estimates_gb: dict[str, float] = field(default_factory=dict)
    stations: int = 0
    stations_dropped: int = 0
    cells: int = 0
    shops: int = 0
    newest_data: datetime | None = None
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"City: {self.city_id} ({'DRY RUN' if self.dry_run else 'built'})"]
        for step, gb in self.estimates_gb.items():
            usd = gb / 1000.0 * ON_DEMAND_USD_PER_TB
            lines.append(f"  estimated scan, {step}: {gb:,.1f} GB (about ${usd:,.2f} on demand)")
        if not self.dry_run:
            lines.append(
                f"  stations: {self.stations} (dropped for short history: "
                f"{self.stations_dropped}), grid cells: {self.cells}, shops: {self.shops}"
            )
            lines.append(f"  newest trip data: {self.newest_data}")
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def load_sql(name: str) -> str:
    return resources.files("cyclesite").joinpath("sql", name).read_text(encoding="utf-8")


def split_statements(sql: str) -> list[str]:
    return [part.strip() for part in sql.split("\n-- ---\n") if part.strip()]


def read_shops_csv(path: str | Path, spec: CitySpec) -> tuple[list[dict[str, Any]], int]:
    """Read ``name,lat,lon`` rows. Returns (rows, count outside the city's bounding box)."""
    rows: list[dict[str, Any]] = []
    outside = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}
        if not {"lat", "lon"} <= set(fields):
            raise ValueError("Shops CSV needs columns: name, lat, lon")
        for i, rec in enumerate(reader, start=2):
            try:
                lat, lon = float(rec[fields["lat"]]), float(rec[fields["lon"]])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Shops CSV line {i}: lat/lon must be numbers") from exc
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"Shops CSV line {i}: coordinates out of range")
            if not (spec.south <= lat <= spec.north and spec.west <= lon <= spec.east):
                outside += 1
            name = (rec.get(fields.get("name", "")) or f"shop{len(rows) + 1}").strip()
            rows.append(
                {
                    "city_id": spec.city_id,
                    "shop_id": f"S{len(rows) + 1:04d}",
                    "name": name[:200],
                    "lat": lat,
                    "lon": lon,
                }
            )
            if len(rows) > MAX_SHOPS:
                raise ValueError(f"Shops CSV has more than {MAX_SHOPS} rows.")
    return rows, outside


class CityBuilder:
    def __init__(
        self,
        client: Any,
        settings: Settings,
        spec: CitySpec,
        max_bytes_osm: int,
        max_bytes_other: int = 20 * 10**9,
    ):
        if not settings.project_id:
            raise ValueError("Set GOOGLE_CLOUD_PROJECT (or pass --project).")
        validate_identifier(settings.dataset, "dataset")
        self.client = client
        self.s = settings
        self.spec = spec
        self.max_osm = max_bytes_osm
        self.max_other = max_bytes_other

    # ---- helpers -----------------------------------------------------------------------
    def _table(self, name: str) -> str:
        return f"{self.s.project_id}.{self.s.dataset}.{validate_identifier(name, 'table')}"

    def _fmt(self, sql: str, **extra: str) -> str:
        return sql.format(
            project=self.s.project_id, dataset=self.s.dataset, city=self.spec.city_id, **extra
        )

    def _config(self, params, cap: int | None, dry: bool = False, destination: str | None = None):
        from google.cloud import bigquery

        cfg = bigquery.QueryJobConfig(query_parameters=list(params), labels=LABELS, dry_run=dry)
        if dry:
            cfg.use_query_cache = False
        else:
            cfg.maximum_bytes_billed = cap
        if destination:
            cfg.destination = destination
            cfg.write_disposition = bigquery.WriteDisposition.WRITE_TRUNCATE
        return cfg

    def _estimate(self, sql: str, params) -> int:
        job = self.client.query(
            sql, job_config=self._config(params, None, dry=True), location=self.s.location
        )
        return int(job.total_bytes_processed or 0)

    def _run(self, sql: str, params, cap: int, destination: str | None = None) -> list[Any]:
        cfg = self._config(params, cap, destination=destination)
        job = self.client.query(sql, job_config=cfg, location=self.s.location)
        return list(job.result())

    def _check_cap(self, step: str, est: int, cap: int) -> None:
        if est > cap:
            raise BuildAborted(
                f"{step} would scan {est / 1e9:,.1f} GB, above the {cap / 1e9:,.1f} GB cap "
                f"(about ${est / 1e12 * ON_DEMAND_USD_PER_TB:,.2f} on demand). Nothing was run. "
                "Re-run with a higher --max-gb only if you accept that cost."
            )

    def _load(
        self, table: str, rows: list[dict], schema: list[tuple[str, str]], truncate: bool
    ) -> None:
        from google.cloud import bigquery

        cfg = bigquery.LoadJobConfig(
            schema=[bigquery.SchemaField(n, t) for n, t in schema],
            write_disposition=(
                bigquery.WriteDisposition.WRITE_TRUNCATE
                if truncate
                else bigquery.WriteDisposition.WRITE_APPEND
            ),
        )
        self.client.load_table_from_json(
            rows, self._table(table), job_config=cfg, location=self.s.location
        ).result()

    # ---- the build ---------------------------------------------------------------------
    def build(self, dry_run: bool = False, shops_csv: str | Path | None = None) -> BuildReport:
        from google.cloud import bigquery

        spec = self.spec
        report = BuildReport(spec.city_id, dry_run)
        shops: list[dict[str, Any]] | None = None
        if shops_csv:
            shops, outside = read_shops_csv(shops_csv, spec)
            if outside:
                report.notes.append(f"{outside} shop(s) lie outside the city bounding box.")

        box = [
            bigquery.ScalarQueryParameter(k, "FLOAT64", getattr(spec, k))
            for k in ("south", "west", "north", "east")
        ]
        osm_sql = load_sql("osm_extract.sql").format(osm_table=OSM_TABLE)
        est = self._estimate(osm_sql, box)
        report.estimates_gb["OpenStreetMap extract"] = est / 1e9
        self._check_cap("The OpenStreetMap extract", est, self.max_osm)

        monthly_sql, monthly_params = None, None
        if spec.bikeshare:
            b = spec.bikeshare
            monthly_sql = load_sql("station_monthly.sql").format(
                trips_table=b.trips_table,
                time_col=b.time_column,
                start_id=b.start_id,
                start_lat=b.start_lat,
                start_lon=b.start_lon,
                end_id=b.end_id,
                end_lat=b.end_lat,
                end_lon=b.end_lon,
            )
            monthly_params = box + [
                bigquery.ScalarQueryParameter("window_months", "INT64", spec.window_months)
            ]
            est_m = self._estimate(monthly_sql, monthly_params)
            report.estimates_gb["station trips"] = est_m / 1e9
            self._check_cap("The station-trips query", est_m, self.max_other)
        if dry_run:
            return report

        self._ensure_schema()
        self._run(osm_sql, box, self.max_osm, destination=self._table(f"osm_{spec.city_id}"))

        stations, newest = [], None
        if monthly_sql and monthly_params:
            rows = [dict(r.items()) for r in self._run(monthly_sql, monthly_params, self.max_other)]
            newest = max((r["newest"] for r in rows), default=None)
            stations, dropped = build_station_demand(rows, spec.window_months)
            report.stations, report.stations_dropped = len(stations), dropped
            if not stations:
                raise BuildAborted("No station has enough monthly history in the trips table.")
        report.newest_data = newest

        lat, lon = make_grid(spec.south, spec.west, spec.north, spec.east, spec.grid_spacing_m)
        report.cells = int(lat.size)
        points = [
            {
                "point_id": f"S_{s.station_id}",
                "kind": "station",
                "lat": s.lat,
                "lon": s.lon,
                "trips_low": s.low,
                "trips_mid": s.mid,
                "trips_high": s.high,
            }
            for s in stations
        ] + [
            {
                "point_id": f"C{i:05d}",
                "kind": "cell",
                "lat": float(la),
                "lon": float(lo),
                "trips_low": None,
                "trips_mid": None,
                "trips_high": None,
            }
            for i, (la, lo) in enumerate(zip(lat, lon, strict=True))
        ]
        self._load(
            f"pts_{spec.city_id}",
            points,
            [
                ("point_id", "STRING"),
                ("kind", "STRING"),
                ("lat", "FLOAT64"),
                ("lon", "FLOAT64"),
                ("trips_low", "FLOAT64"),
                ("trips_mid", "FLOAT64"),
                ("trips_high", "FLOAT64"),
            ],
            truncate=True,
        )

        city_param = bigquery.ScalarQueryParameter("city", "STRING", spec.city_id)
        if shops is not None:
            self._run(
                self._fmt(
                    "DELETE FROM `{project}.{dataset}.city_existing_shops` WHERE city_id = @city"
                ),
                [city_param],
                self.max_other,
            )
            if shops:
                self._load(
                    "city_existing_shops",
                    shops,
                    [
                        ("city_id", "STRING"),
                        ("shop_id", "STRING"),
                        ("name", "STRING"),
                        ("lat", "FLOAT64"),
                        ("lon", "FLOAT64"),
                    ],
                    truncate=False,
                )
            report.shops = len(shops)

        self._run(
            self._fmt(load_sql("point_features.sql")),
            [
                city_param,
                bigquery.ScalarQueryParameter("radius_m", "FLOAT64", spec.feature_radius_m),
                bigquery.ScalarQueryParameter("name", "STRING", spec.name),
                bigquery.ScalarQueryParameter("demand_kind", "STRING", spec.demand_kind),
                bigquery.ScalarQueryParameter("newest_data", "TIMESTAMP", newest),
            ],
            self.max_other,
        )
        log.info("built city %s", spec.city_id)
        return report

    def _ensure_schema(self) -> None:
        from google.cloud import bigquery

        ds = bigquery.Dataset(f"{self.s.project_id}.{self.s.dataset}")
        ds.location = self.s.location
        self.client.create_dataset(ds, exists_ok=True)
        for stmt in split_statements(load_sql("schema.sql")):
            self._run(self._fmt(stmt), [], self.max_other)
