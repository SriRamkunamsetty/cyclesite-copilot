import pytest

from cycle_agent import tools

from .helpers import FakeContext

pytestmark = pytest.mark.usefixtures("synthetic_env")


def test_list_cities_and_freshness():
    out = tools.list_cities()
    assert out["status"] == "ok" and {c["city_id"] for c in out["cities"]} >= {"demo_a"}
    ctx = FakeContext()
    fr = tools.check_data_freshness("demo_b", ctx)
    assert fr["level"] == "expired" and ctx.state["freshness"]["level"] == "expired"
    assert tools.check_data_freshness("nope", ctx)["status"] == "error"


def test_choose_sites_happy_path_stores_analysis():
    ctx = FakeContext()
    out = tools.choose_sites("demo_a", ctx, k=3)
    assert out["status"] == "ok"
    a = out["analysis"]
    assert len(a["picks"]) == 3 and a["demand_source"] == "observed"
    assert a["captured_by_case"]["low"] < a["captured_by_case"]["high"]
    assert ctx.state["analysis"] == a and "stability" in a


def test_expired_data_requires_explicit_acknowledgement():
    ctx = FakeContext()
    first = tools.choose_sites("demo_b", ctx, k=2)
    assert first["status"] == "needs_confirmation" and "analysis" not in ctx.state
    second = tools.choose_sites("demo_b", ctx, k=2, acknowledge_stale_data=True)
    assert second["status"] == "ok"


def test_invalid_parameters_return_errors_not_exceptions():
    ctx = FakeContext()
    assert tools.choose_sites("demo_a", ctx, k=0)["status"] == "error"
    assert tools.choose_sites("demo_a", ctx, decay_m=-5)["status"] == "error"
    assert tools.choose_sites("missing", ctx)["status"] == "error"


def test_city_without_bikeshare_needs_a_source_city_and_reports_transfer():
    ctx = FakeContext()
    assert tools.choose_sites("demo_c", ctx)["status"] == "error"
    assert tools.choose_sites("demo_c", ctx, demand_model_city_id="demo_c")["status"] == "error"
    out = tools.choose_sites("demo_c", ctx, demand_model_city_id="demo_a")
    assert out["status"] == "ok" and out["analysis"]["demand_source"] == "modelled"
    assert out["transfer"]["level"] in {"caution", "unlike"}
    assert ctx.state["transfer"]["source_city"] == "demo_a"


def test_backtest_within_and_cross_city():
    ctx = FakeContext()
    within = tools.run_backtest("demo_a", ctx)
    assert within["status"] == "ok" and within["backtest"]["setting"] == "within_city_holdout"
    cross = tools.run_backtest("demo_a", ctx, test_city_id="demo_b")
    assert cross["backtest"]["setting"] == "cross_city"
    assert tools.run_backtest("demo_c", ctx)["status"] == "error"


def test_red_team_requires_analysis_then_reports_findings():
    ctx = FakeContext()
    assert tools.red_team_picks(ctx)["status"] == "error"
    tools.choose_sites("demo_a", ctx, k=3)
    out = tools.red_team_picks(ctx)
    assert out["status"] == "ok"
    assert all(f["severity"] in {"high", "medium", "low"} for f in out["findings"])


def test_new_analysis_clears_stale_critic_state():
    ctx = FakeContext()
    tools.choose_sites("demo_a", ctx, k=2)
    tools.red_team_picks(ctx)
    ctx.state["critic_notes"] = "old"
    tools.choose_sites("demo_a", ctx, k=3)
    assert not ctx.state["red_team"] and not ctx.state["critic_notes"]


def test_memo_is_built_from_stored_numbers_and_labels_synthetic_data():
    ctx = FakeContext()
    assert tools.build_decision_memo(ctx)["status"] == "error"
    tools.choose_sites("demo_a", ctx, k=3)
    tools.run_backtest("demo_a", ctx)
    memo = tools.build_decision_memo(ctx, question="Where next?", critic_notes="Looks fragile.")[
        "memo_markdown"
    ]
    for needle in (
        "# Site decision memo",
        "SYNTHETIC DEMO DATA",
        "Where next?",
        "Backtest",
        "Critic's view",
        "Audit trail",
        "## Recommended sites",
    ):
        assert needle in memo
    for p in ctx.state["analysis"]["picks"]:
        assert p["site_id"] in memo


def test_transfer_check_does_not_leak_between_cities():
    ctx = FakeContext()
    tools.choose_sites("demo_c", ctx, demand_model_city_id="demo_a")
    assert ctx.state["transfer"]["level"]
    tools.choose_sites("demo_a", ctx)
    assert ctx.state["transfer"] is None


def test_backtest_on_old_data_carries_a_timing_caveat():
    ctx = FakeContext()
    out = tools.run_backtest("demo_b", ctx)["backtest"]
    assert any("snapshot" in n for n in out["notes"])
    assert not any("snapshot" in n for n in tools.run_backtest("demo_a", ctx)["backtest"]["notes"])
