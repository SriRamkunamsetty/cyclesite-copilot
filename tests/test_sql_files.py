"""Every SQL template must at least parse as BigQuery SQL, and the guard must not be needed
for them: they only run through our own build code, never through the agent."""

import pytest

sqlglot = pytest.importorskip("sqlglot")

from cyclesite.build import OSM_TABLE, load_sql, split_statements  # noqa: E402


def parse(sql):
    return sqlglot.parse(sql, read="bigquery")


def test_schema_statements_parse():
    sql = load_sql("schema.sql").format(project="p", dataset="d")
    stmts = split_statements(sql)
    assert len(stmts) == 3
    for s in stmts:
        assert parse(s)[0] is not None


def test_osm_extract_parses_and_keeps_the_spatial_filter():
    sql = load_sql("osm_extract.sql").format(osm_table=OSM_TABLE)
    assert parse(sql)[0] is not None
    assert "ST_INTERSECTSBOX" in sql and "@west" in sql


def test_station_monthly_parses_and_is_parameterised():
    sql = load_sql("station_monthly.sql").format(
        trips_table="bigquery-public-data.new_york_citibike.citibike_trips",
        time_col="starttime",
        start_id="a",
        start_lat="b",
        start_lon="c",
        end_id="d",
        end_lat="e",
        end_lon="f",
    )
    assert parse(sql)[0] is not None
    for p in ("@window_months", "@south", "@north", "@west", "@east"):
        assert p in sql


def test_point_features_script_parses_statement_by_statement():
    sql = load_sql("point_features.sql").format(project="p", dataset="d", city="nyc")
    stmts = [s for s in parse(sql) if s is not None]
    assert len(stmts) >= 5
    assert "@radius_m" in sql and sql.count("@city") >= 4
    assert "BEGIN TRANSACTION" in sql and "COMMIT TRANSACTION" in sql
