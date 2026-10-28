from datetime import UTC, datetime

import pytest

from cyclesite.build import BuildAborted, CityBuilder, read_shops_csv
from cyclesite.cities import load_cities
from cyclesite.config import Settings

NEWEST = datetime(2018, 5, 31, tzinfo=UTC)


class Job:
    def __init__(self, rows=None, bytes_=0):
        self._rows, self.total_bytes_processed = rows or [], bytes_

    def result(self):
        return self._rows


class FakeClient:
    """Records every call; returns canned monthly rows for the station query."""

    def __init__(self, dry_bytes=1_000_000_000, monthly=None):
        self.dry_bytes, self.monthly = dry_bytes, monthly or []
        self.queries, self.loads, self.datasets = [], [], []

    def query(self, sql, job_config, location):
        dry = bool(job_config.dry_run)
        self.queries.append(
            {
                "sql": sql,
                "dry": dry,
                "cap": job_config.maximum_bytes_billed,
                "dest": job_config.destination,
                "labels": job_config.labels,
                "params": {p.name: p.value for p in job_config.query_parameters},
                "location": location,
            }
        )
        if dry:
            return Job(bytes_=self.dry_bytes)
        if "ev.station_id" in sql and "bounds" in sql:
            return Job(self.monthly)
        return Job()

    def create_dataset(self, ds, exists_ok):
        self.datasets.append((ds.dataset_id, ds.location, exists_ok))

    def load_table_from_json(self, rows, table, job_config, location):
        self.loads.append(
            {"rows": rows, "table": table, "disposition": job_config.write_disposition}
        )
        return Job()


def monthly_rows(n_stations=3):
    out = []
    for s in range(n_stations):
        for m in range(1, 13):
            out.append(
                {
                    "station_id": str(100 + s),
                    "lat": 40.7 + s * 0.01,
                    "lon": -74.0,
                    "month": datetime(2017 + (m + 4) // 12, (m + 4) % 12 + 1, 1).date(),
                    "trips": 100 + 10 * m,
                    "newest": NEWEST,
                }
            )
    return out


def settings():
    return Settings(backend="bigquery", project_id="proj", dataset="cs", location="US")


def builder(client, city="nyc", max_gb=100):
    return CityBuilder(client, settings(), load_cities()[city], int(max_gb * 1e9))


def test_dry_run_only_estimates_and_changes_nothing():
    c = FakeClient(dry_bytes=5_000_000_000)
    rep = builder(c).build(dry_run=True)
    assert all(q["dry"] for q in c.queries) and not c.loads and not c.datasets
    assert set(rep.estimates_gb) == {"OpenStreetMap extract", "station trips"}
    assert "DRY RUN" in rep.summary() and "$" in rep.summary()


def test_over_cap_aborts_before_any_real_query():
    c = FakeClient(dry_bytes=900 * 10**9)
    with pytest.raises(BuildAborted) as e:
        builder(c, max_gb=100).build()
    assert "Nothing was run" in str(e.value)
    assert all(q["dry"] for q in c.queries)


def test_full_bikeshare_build_order_caps_and_labels():
    c = FakeClient(monthly=monthly_rows())
    rep = builder(c).build()
    real = [q for q in c.queries if not q["dry"]]
    assert c.datasets == [("cs", "US", True)]
    assert all(q["cap"] for q in real) and all(
        q["labels"]["app"] == "cyclesite-copilot" for q in c.queries
    )
    assert all(q["location"] == "US" for q in c.queries)
    kinds = [
        ("osm" if q["dest"] else "script" if "BEGIN TRANSACTION" in q["sql"] else "other")
        for q in real
    ]
    assert kinds.index("osm") < kinds.index("script") and kinds[-1] == "script"
    dest = real[[q["dest"] is not None for q in real].index(True)]["dest"]
    assert dest.table_id == "osm_nyc" and dest.dataset_id == "cs"
    assert rep.stations == 3 and rep.cells > 1000 and rep.newest_data == NEWEST
    pts = c.loads[0]["rows"]
    assert {r["kind"] for r in pts} == {"station", "cell"}
    assert all(r["trips_mid"] is None for r in pts if r["kind"] == "cell")
    last = real[-1]["params"]
    assert last["city"] == "nyc" and last["demand_kind"] == "observed"
    assert last["newest_data"] == NEWEST


def test_city_without_bikeshare_skips_trip_query_and_is_modelled():
    c = FakeClient()
    rep = builder(c, "pune").build()
    assert rep.stations == 0 and "station trips" not in rep.estimates_gb
    assert not any("ev.station_id" in q["sql"] for q in c.queries)
    assert c.queries[-1]["params"]["demand_kind"] == "modelled"


def test_no_station_with_enough_history_aborts():
    c = FakeClient(monthly=monthly_rows()[:5])
    with pytest.raises(BuildAborted):
        builder(c).build()


def test_shops_csv_is_validated_then_replaces_existing_rows(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("Name,Lat,Lon\nCafe A,18.52,73.85\nFar,10.0,10.0\n")
    spec = load_cities()["pune"]
    rows, outside = read_shops_csv(f, spec)
    assert len(rows) == 2 and outside == 1 and rows[0]["shop_id"] == "S0001"
    c = FakeClient()
    rep = builder(c, "pune").build(shops_csv=f)
    assert rep.shops == 2 and any("outside" in n for n in rep.notes)
    assert any("DELETE FROM" in q["sql"] and "city_existing_shops" in q["sql"] for q in c.queries)
    assert c.loads[-1]["table"].endswith("city_existing_shops")


@pytest.mark.parametrize("content", ["a,b\n1,2\n", "lat,lon\nx,1\n", "lat,lon\n95,1\n"])
def test_bad_shops_csv_is_rejected(tmp_path, content):
    f = tmp_path / "s.csv"
    f.write_text(content)
    with pytest.raises(ValueError):
        read_shops_csv(f, load_cities()["pune"])


def test_builder_requires_project():
    with pytest.raises(ValueError):
        CityBuilder(FakeClient(), Settings(project_id=None), load_cities()["nyc"], 1)
