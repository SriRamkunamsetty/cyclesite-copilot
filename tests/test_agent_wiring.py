import asyncio
from types import SimpleNamespace

from cyclesite.config import Settings

from .helpers import FakeContext


def test_synthetic_agent_has_function_tools_critic_and_no_bigquery(synthetic_env):
    from cycle_agent.agent import build_root_agent

    agent = build_root_agent(Settings(backend="synthetic"))
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in agent.tools}
    assert {
        "choose_sites",
        "run_backtest",
        "red_team_picks",
        "build_decision_memo",
        "site_critic",
    } <= names
    assert agent.before_tool_callback is None
    assert not any("McpToolset" in type(t).__name__ for t in agent.tools)


def test_bigquery_agent_gets_readonly_mcp_toolset_and_guard(monkeypatch):
    from cycle_agent import services
    from cycle_agent.agent import build_root_agent
    from cycle_agent.mcp import ALLOWED_TOOLS

    monkeypatch.setenv("CYCLESITE_BACKEND", "bigquery")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    services.reset_caches()
    try:
        agent = build_root_agent(services.get_settings())
        assert any(type(t).__name__ == "McpToolset" for t in agent.tools)
        assert agent.before_tool_callback is not None
        assert "execute_sql" not in ALLOWED_TOOLS and "execute_sql_readonly" in ALLOWED_TOOLS
    finally:
        services.reset_caches()


def test_guard_hook_blocks_destructive_sql_without_touching_bigquery(monkeypatch):
    """The estimator would need credentials; a blocked query must never reach it."""
    from cycle_agent import services

    monkeypatch.setenv("CYCLESITE_BACKEND", "bigquery")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    services.reset_caches()
    try:
        guard = services.get_guard()
        ctx = FakeContext()
        out = asyncio.run(
            guard.before_tool(
                SimpleNamespace(name="execute_sql_readonly"),
                {"query": "DROP TABLE x", "projectId": "proj"},
                ctx,
            )
        )
        assert out["status"] == "blocked_by_guardrail"
        assert ctx.state["audit"][0]["allowed"] is False
    finally:
        services.reset_caches()


def test_adc_headers_use_default_credentials(monkeypatch):
    import google.auth

    from cycle_agent.mcp import adc_headers

    class Creds:
        valid, token = True, "tok"

    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (Creds(), "p1"))
    assert adc_headers(None) == {"Authorization": "Bearer tok", "x-goog-user-project": "p1"}


def test_prompts_carry_the_non_negotiable_rules():
    from cycle_agent import prompts

    text = prompts.ORCHESTRATOR.lower()
    for needle in (
        "never compute",
        "acknowledge_stale_data=true",
        "synthetic",
        "never call it revenue",
        "no reliable lift",
        "read-only",
    ):
        assert needle in text, needle
    assert "only the numbers" in prompts.CRITIC.lower()
