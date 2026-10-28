import asyncio
from types import SimpleNamespace

import pytest

from cyclesite.guardrails import (
    BigQueryEstimator,
    SqlGuard,
    extract_sql,
    strip_noise,
    validate_sql,
)

from .helpers import FakeContext

OK = "SELECT id FROM `p.d.t` WHERE x > 1"


@pytest.mark.parametrize(
    "sql",
    [
        OK,
        "WITH a AS (SELECT 1) SELECT * FROM a;",
        "(SELECT 1)",
        "SELECT 'DROP TABLE x' AS note",
        "SELECT 1 -- DELETE everything",
        "SELECT created_at, update_count FROM t",
    ],
)
def test_accepts_read_only_queries(sql):
    assert validate_sql(sql) == []


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE t",
        "DELETE FROM t WHERE true",
        "INSERT INTO t VALUES (1)",
        "CREATE TABLE x AS SELECT 1",
        "SELECT 1; SELECT 2",
        "SELECT 1; DROP TABLE t",
        "UPDATE t SET a = 1",
        "MERGE t USING s ON true WHEN MATCHED THEN DELETE",
        "EXPORT DATA OPTIONS(uri='gs://b/x') AS SELECT 1",
        "CALL proc()",
        "",
        "   ;  ",
        "DECLARE x INT64; SELECT x",
    ],
)
def test_rejects_writes_scripts_and_empty(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT '#'; DROP TABLE t",  # comment char hidden inside a string
        "SELECT '--'; DROP TABLE t",
        'SELECT "#"; DELETE FROM t WHERE true',
        "SELECT 1 /* unterminated",
        "SELECT 'unterminated",
        "SELECT '''x'''; DROP TABLE t",
        "SELECT r'\\'; DROP TABLE t; --'",
    ],
)
def test_comment_and_string_tricks_do_not_bypass(sql):
    assert validate_sql(sql)


def test_strip_noise_single_pass():
    assert "DROP" not in strip_noise("SELECT '#' , 'DROP'")
    assert strip_noise("SELECT 1 # c\nFROM t").count("FROM") == 1
    assert "planet_features" in strip_noise("SELECT 1 FROM `a.planet_features`", True)
    assert "planet_features" not in strip_noise("SELECT 1 FROM `a.planet_features`")


def test_planet_tables_need_a_spatial_filter():
    bad = "SELECT * FROM `bigquery-public-data.geo_openstreetmap.planet_features`"
    assert any("bounding" in p for p in validate_sql(bad))
    good = bad + " WHERE ST_INTERSECTSBOX(geometry, -74.3, 40.5, -73.7, 40.9)"
    assert validate_sql(good) == []


class FixedBytes:
    def __init__(self, n=None, exc=None):
        self.n, self.exc = n, exc

    def estimate_bytes(self, sql):
        if self.exc:
            raise self.exc
        return self.n


def test_guard_blocks_over_budget_and_fails_closed():
    assert SqlGuard(FixedBytes(5), 10).check(OK).allowed
    big = SqlGuard(FixedBytes(50e9), 10e9).check(OK)
    assert not big.allowed and "cap" in big.reasons[0] and big.estimated_bytes == 50e9
    broken = SqlGuard(FixedBytes(exc=RuntimeError("no creds")), 10).check(OK)
    assert not broken.allowed and "Dry run failed" in broken.reasons[0]
    assert not SqlGuard(FixedBytes(1), 10).check("DROP TABLE t").allowed


def run_before(guard, name, args):
    ctx = FakeContext()
    out = asyncio.run(guard.before_tool(SimpleNamespace(name=name), args, ctx))
    return out, ctx


def test_before_tool_ignores_other_tools_and_audits_guarded_ones():
    guard = SqlGuard(FixedBytes(1_000), 10_000, "my-proj")
    out, ctx = run_before(guard, "list_table_ids", {"datasetId": "x"})
    assert out is None and "audit" not in ctx.state
    out, ctx = run_before(guard, "execute_sql_readonly", {"query": OK, "projectId": "my-proj"})
    assert out is None
    entry = ctx.state["audit"][0]
    assert entry["allowed"] and entry["estimated_bytes"] == 1_000 and len(entry["sql_sha256"]) == 16


def test_before_tool_blocks_and_records_reasons():
    guard = SqlGuard(FixedBytes(1_000), 10_000, "my-proj")
    out, ctx = run_before(guard, "execute_sql", {"query": "DROP TABLE t", "projectId": "my-proj"})
    assert out["status"] == "blocked_by_guardrail"
    assert ctx.state["audit"][0]["allowed"] is False
    out, _ = run_before(guard, "execute_sql_readonly", {"query": OK, "projectId": "other"})
    assert out and "my-proj" in out["reasons"][0]
    out, _ = run_before(guard, "execute_sql_readonly", {"unexpected": 1})
    assert out and "Could not find the SQL" in out["reasons"][0]


def test_extract_sql_finds_common_argument_names():
    assert extract_sql({"query": "SELECT 1"}) == "SELECT 1"
    assert extract_sql({"x": "  WITH a AS (SELECT 1) SELECT * FROM a"})
    assert extract_sql({"datasetId": "abc"}) is None


def test_bigquery_estimator_uses_dry_run_without_cache():
    seen = {}

    class Client:
        def query(self, sql, job_config, location):
            seen.update(dry=job_config.dry_run, cache=job_config.use_query_cache, loc=location)
            return SimpleNamespace(total_bytes_processed=1234)

    assert BigQueryEstimator(Client(), "US").estimate_bytes("SELECT 1") == 1234
    assert seen == {"dry": True, "cache": False, "loc": "US"}
