"""The eval files must load with the installed ADK's own data models."""

import json
from pathlib import Path

import pytest

pytest.importorskip("google.adk")

from google.adk.evaluation.eval_config import EvalConfig  # noqa: E402
from google.adk.evaluation.eval_set import EvalSet  # noqa: E402

EVALS = Path(__file__).resolve().parent.parent / "evals"


def test_evalset_loads_and_expected_tools_exist():
    from cycle_agent import tools

    ev = EvalSet.model_validate_json((EVALS / "cyclesite.evalset.json").read_text())
    assert len(ev.eval_cases) >= 5
    known = {n for n in dir(tools) if not n.startswith("_")}
    for case in ev.eval_cases:
        assert case.rubrics, case.eval_id
        for inv in case.conversation or []:
            for use in getattr(inv.intermediate_data, "tool_uses", []) or []:
                assert use.name in known, (case.eval_id, use.name)


def test_eval_config_loads():
    cfg = EvalConfig.model_validate(json.loads((EVALS / "test_config.json").read_text()))
    assert "tool_trajectory_avg_score" in cfg.criteria
