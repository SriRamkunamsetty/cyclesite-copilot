"""Root agent: orchestrates deterministic tools, a critic agent and the BigQuery MCP server."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool
from google.genai import types

from cyclesite.config import Settings

from . import prompts, tools
from .services import get_guard, get_settings

_FUNCTION_TOOLS = [
    tools.list_cities,
    tools.check_data_freshness,
    tools.assess_transfer_risk,
    tools.choose_sites,
    tools.run_backtest,
    tools.red_team_picks,
    tools.build_decision_memo,
]


def build_critic(settings: Settings) -> LlmAgent:
    return LlmAgent(
        name="site_critic",
        model=settings.model,
        description="Argues, using only supplied numbers, why a site recommendation could fail.",
        instruction=prompts.CRITIC,
        generate_content_config=types.GenerateContentConfig(temperature=0.3),
    )


def build_root_agent(settings: Settings | None = None) -> LlmAgent:
    settings = settings or get_settings()
    agent_tools: list = list(_FUNCTION_TOOLS) + [AgentTool(agent=build_critic(settings))]
    before_tool = None
    if settings.backend == "bigquery":
        from .mcp import bigquery_toolset

        agent_tools.append(bigquery_toolset())
        before_tool = get_guard().before_tool  # every execute_sql* call is checked in code
    return LlmAgent(
        name="cyclesite_copilot",
        model=settings.model,
        description=(
            "Chooses cyclist-friendly shop sites, tests the method, and says how sure it is."
        ),
        instruction=prompts.ORCHESTRATOR,
        tools=agent_tools,
        before_tool_callback=before_tool,
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
    )


root_agent = build_root_agent()
