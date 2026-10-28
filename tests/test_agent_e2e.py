"""Run the real ADK agent end to end with a scripted stand-in for Gemini.

This proves the wiring that unit tests cannot: ADK builds each tool schema, dispatches the calls,
passes session state between tools, runs the critic as a sub-agent, and the final memo is built
from stored numbers. It does NOT test how well real Gemini follows the instructions; that needs
the evaluation set in ``evals/`` and a Google Cloud project.
"""

import asyncio

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.adk.models.llm_request import LlmRequest
from google.adk.runners import InMemoryRunner
from google.genai import types

from cyclesite.config import Settings

SCRIPT = [
    ("list_cities", {}),
    ("check_data_freshness", {"city_id": "demo_a"}),
    ("choose_sites", {"city_id": "demo_a", "k": 2}),
    ("run_backtest", {"train_city_id": "demo_a"}),
    ("red_team_picks", {}),
    ("site_critic", {"request": "Picks and findings go here."}),
    ("build_decision_memo", {"question": "Where next?", "critic_notes": "Scripted critic."}),
]


class ScriptedLlm(BaseLlm):
    model: str = "scripted"

    @property
    def capabilities(self):
        from google.adk.models.llm_capabilities import LlmCapabilities

        return LlmCapabilities()

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        system = str(llm_request.config.system_instruction or "")
        if "sceptical reviewer" in system:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(text="Scripted critic: fragile pick. Overall confidence: low.")
                    ],
                )
            )
            return
        done = sum(1 for c in llm_request.contents for p in (c.parts or []) if p.function_response)
        if done < len(SCRIPT):
            name, args = SCRIPT[done]
            part = types.Part(function_call=types.FunctionCall(name=name, args=args))
        else:
            part = types.Part(text="Done. See the memo.")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def run(agent):
    runner = InMemoryRunner(agent=agent, app_name="cyclesite_test")

    async def go():
        session = await runner.session_service.create_session(
            app_name="cyclesite_test", user_id="u"
        )
        calls, responses = [], {}
        msg = types.Content(role="user", parts=[types.Part(text="Where should we open 2 shops?")])
        async for ev in runner.run_async(user_id="u", session_id=session.id, new_message=msg):
            for p in ev.content.parts if ev.content else []:
                if p.function_call:
                    calls.append(p.function_call.name)
                if p.function_response:
                    responses[p.function_response.name] = p.function_response.response
        final = await runner.session_service.get_session(
            app_name="cyclesite_test", user_id="u", session_id=session.id
        )
        return calls, responses, final.state

    return asyncio.run(go())


@pytest.mark.usefixtures("synthetic_env")
def test_full_workflow_through_adk_runtime(monkeypatch):
    from cycle_agent import agent as agent_mod

    monkeypatch.setattr(
        agent_mod.LlmAgent, "model_post_init", agent_mod.LlmAgent.model_post_init, raising=False
    )
    settings = Settings(backend="synthetic", model="scripted")
    root = agent_mod.build_root_agent(settings)
    root.model = ScriptedLlm()
    for tool in root.tools:
        if getattr(tool, "agent", None) is not None:
            tool.agent.model = ScriptedLlm()
    calls, responses, state = run(root)
    assert calls == [name for name, _ in SCRIPT]
    assert responses["choose_sites"]["status"] == "ok"
    assert len(state["analysis"]["picks"]) == 2
    assert state["backtest"]["setting"] == "within_city_holdout"
    assert state["red_team"] is not None
    memo = responses["build_decision_memo"]["memo_markdown"]
    assert "Scripted critic" in memo and "SYNTHETIC DEMO DATA" in memo
    assert "Scripted critic: fragile pick" in str(responses["site_critic"])
