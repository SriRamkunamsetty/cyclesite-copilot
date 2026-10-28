"""Function tools the orchestrator calls. All numbers come from here, never from the LLM.

Results are also written to session state so the decision memo is built from stored computation
rather than from numbers the model re-typed.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from google.adk.tools import ToolContext

from cyclesite.backtest import run_backtest as _run_backtest
from cyclesite.backtest import split_indices
from cyclesite.freshness import assess_freshness
from cyclesite.memo import build_memo
from cyclesite.model import fit_demand_model
from cyclesite.optimizer import DistanceCache, OptimizerConfig, select_sites, stability_report
from cyclesite.problem import build_problem
from cyclesite.redteam import red_team
from cyclesite.transfer import assess_transfer

from .services import get_repository, get_settings


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "message": message, **extra}


def _freshness(city):
    s = get_settings()
    return assess_freshness(city.newest_data, None, s.freshness_warn_days, s.freshness_expired_days)


def list_cities() -> dict[str, Any]:
    """List the cities that have been prepared, and whether each has real bikeshare demand.

    Call this first. Cities with has_observed_demand=true can be used to test the method;
    cities without it rely on a demand model fitted on one that does.
    """
    try:
        return {"status": "ok", "cities": get_repository().list_cities()}
    except Exception as exc:
        return _error(f"Could not list cities: {exc}")


def check_data_freshness(city_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Report how old the newest data for a city is. Always call before choosing sites.

    Args:
        city_id: City identifier from list_cities.
    """
    try:
        city = get_repository().load_city(city_id)
    except KeyError as exc:
        return _error(str(exc))
    fr = _freshness(city)
    tool_context.state["freshness"] = fr.to_dict()
    return {"status": "ok", "city_id": city_id, **fr.to_dict()}


def choose_sites(
    city_id: str,
    tool_context: ToolContext,
    k: int = 3,
    min_rideability: float = 0.35,
    decay_m: float = 900.0,
    min_separation_m: float = 400.0,
    demand_model_city_id: str = "",
    acknowledge_stale_data: bool = False,
) -> dict[str, Any]:
    """Pick k new shop sites that add the most NEW demand without cannibalising each other.

    Ranks on the cautious (low) demand case, avoids low-rideability spots, and counts shops that
    already exist. Also re-runs under different assumptions to show how stable the picks are.

    Args:
        city_id: City identifier from list_cities.
        k: Number of new shops (1-10).
        min_rideability: Minimum cyclist-friendliness (0-1) a site must have.
        decay_m: Distance (metres) over which a shop's pull fades; a tunable assumption.
        min_separation_m: Minimum spacing between shops in metres.
        demand_model_city_id: For a city WITHOUT bikeshare data, the bikeshare city whose
            usage should be used to estimate demand. Leave empty otherwise.
        acknowledge_stale_data: Set true ONLY after the user has agreed to proceed with
            expired data.
    """
    try:
        cfg = OptimizerConfig(
            k=int(k),
            min_rideability=min_rideability,
            decay_m=decay_m,
            min_separation_m=min_separation_m,
        )
    except ValueError as exc:
        return _error(str(exc))
    repo = get_repository()
    try:
        city = repo.load_city(city_id)
    except KeyError as exc:
        return _error(str(exc))
    fr = _freshness(city)
    tool_context.state["freshness"] = fr.to_dict()
    if fr.requires_acknowledgement and not acknowledge_stale_data:
        return {
            "status": "needs_confirmation",
            "message": fr.message + " Ask the user whether to proceed anyway; if they agree, "
            "call again with acknowledge_stale_data=true.",
            "freshness": fr.to_dict(),
        }

    model, transfer = None, None
    tool_context.state["transfer"] = None  # never reuse another city's transfer check
    if not city.has_observed_demand:
        if not demand_model_city_id:
            return _error(
                "This city has no bikeshare demand data. Pass demand_model_city_id "
                "(a city with has_observed_demand=true)."
            )
        try:
            source = repo.load_city(demand_model_city_id)
        except KeyError as exc:
            return _error(str(exc))
        stations = source.stations()
        if stations is None:
            return _error(f"'{demand_model_city_id}' has no observed demand either.")
        model = fit_demand_model(stations.features, stations.trips)
        target = city.points.take(city.points.mask("cell"))
        transfer = assess_transfer(stations.features, target.features)
        tool_context.state["transfer"] = {
            "source_city": demand_model_city_id,
            "target_city": city_id,
            **transfer.to_dict(),
        }
    try:
        problem = build_problem(city, demand_model=model)
        cache = DistanceCache(problem.demand, problem.candidates, problem.existing)
        result = select_sites(problem.demand, problem.candidates, problem.existing, cfg, cache)
        stab = stability_report(
            problem.demand, problem.candidates, problem.existing, cfg, cache=cache
        )
    except ValueError as exc:
        return _error(str(exc))

    analysis = {
        "city_id": city_id,
        "city_name": city.name,
        "source": city.source,
        "demand_source": problem.demand.source,
        "demand_note": "; ".join(problem.notes) or "observed bikeshare trips with a forecast range",
        "existing_shops": len(problem.existing),
        "freshness": fr.to_dict(),
        "config": {
            "k": cfg.k,
            "decay_m": cfg.decay_m,
            "max_radius_m": cfg.max_radius_m,
            "min_rideability": cfg.min_rideability,
            "min_separation_m": cfg.min_separation_m,
            "rideability_floor": cfg.rideability_floor,
            "case": cfg.demand_case,
        },
        "picks": [
            {
                "rank": p.rank,
                "site_id": p.site_id,
                "lat": round(p.lat, 6),
                "lon": round(p.lon, 6),
                "rideability": round(p.rideability, 3),
                "marginal_gain": round(p.marginal_gain, 1),
                "standalone_gain": round(p.standalone_gain, 1),
                "overlap_share": round(p.overlap_share, 3),
                "coverage_after": round(p.coverage_after, 4),
                "gain_by_case": {c: round(v, 1) for c, v in p.gain_by_case.items()},
            }
            for p in result.picks
        ],
        "total_weighted_demand": round(result.total_weighted_demand, 1),
        "baseline_coverage": round(result.baseline_coverage, 4),
        "final_coverage": round(result.final_coverage, 4),
        "captured_by_case": {c: round(v, 1) for c, v in result.captured_by_case.items()},
        "warnings": result.warnings,
        "stability": {
            "runs": stab.runs,
            "mean_jaccard": round(stab.mean_jaccard, 3),
            "pick_frequency": {k_: round(v, 3) for k_, v in stab.pick_frequency.items()},
        },
    }
    tool_context.state["analysis"] = analysis
    for stale in ("red_team", "critic_notes"):
        tool_context.state[stale] = None  # ADK's State has no pop(); None means "recompute"
    return {
        "status": "ok",
        "analysis": analysis,
        "transfer": transfer.to_dict() if transfer else None,
    }


def run_backtest(
    train_city_id: str, tool_context: ToolContext, test_city_id: str = ""
) -> dict[str, Any]:
    """Check whether the scoring predicts REAL bikeshare usage on stations it has not seen.

    Leave test_city_id empty for a within-city holdout (70/30 split). Give a second city to test
    transfer across cities (train on the first, test on the second).

    Args:
        train_city_id: A city with has_observed_demand=true.
        test_city_id: Optional second city with observed demand.
    """
    repo = get_repository()
    try:
        train_city = repo.load_city(train_city_id)
        train = train_city.stations()
        if train is None:
            return _error(f"'{train_city_id}' has no observed demand to backtest on.")
        if test_city_id:
            test = repo.load_city(test_city_id).stations()
            if test is None:
                return _error(f"'{test_city_id}' has no observed demand to backtest on.")
            report = _run_backtest(train, test, "cross_city")
        else:
            tr_idx, te_idx = split_indices(train.n)
            report = _run_backtest(
                train.subset(tr_idx), train.subset(te_idx), "within_city_holdout"
            )
    except (KeyError, ValueError) as exc:
        return _error(str(exc))
    out = report.to_dict()
    fr = _freshness(train_city)
    if fr.level in {"stale", "expired"}:
        out["notes"].append(
            f"Trips are from before {fr.newest_data}, but the cycle-lane map is a current "
            "snapshot. Lanes built since then blur this test, so treat the verdict as indicative."
        )
    tool_context.state["backtest"] = out
    return {"status": "ok", "backtest": out}


def assess_transfer_risk(
    source_city_id: str, target_city_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    """Judge whether a target city looks like a city the method was tested on.

    Args:
        source_city_id: City with observed bikeshare demand (where the method is tested).
        target_city_id: City you want recommendations for.
    """
    repo = get_repository()
    try:
        src = repo.load_city(source_city_id).stations()
        if src is None:
            return _error(f"'{source_city_id}' has no observed demand.")
        tgt_city = repo.load_city(target_city_id)
    except KeyError as exc:
        return _error(str(exc))
    target_pts = tgt_city.points.take(tgt_city.points.mask("cell"))
    if target_pts.ids.size == 0:
        target_pts = tgt_city.points
    res = assess_transfer(src.features, target_pts.features)
    out = {"source_city": source_city_id, "target_city": target_city_id, **res.to_dict()}
    tool_context.state["transfer"] = out
    return {"status": "ok", "transfer": out}


def red_team_picks(tool_context: ToolContext) -> dict[str, Any]:
    """List concrete, number-backed reasons the current picks could fail.

    Call after choose_sites. Pass the findings to the site_critic agent.
    """
    analysis = tool_context.state.get("analysis")
    if not analysis:
        return _error("Run choose_sites first.")
    findings = red_team(
        analysis, tool_context.state.get("backtest"), tool_context.state.get("transfer")
    )
    tool_context.state["red_team"] = findings
    return {"status": "ok", "findings": findings}


def build_decision_memo(
    tool_context: ToolContext, question: str = "", critic_notes: str = ""
) -> dict[str, Any]:
    """Assemble the final decision memo from stored results, including every query that ran.

    Args:
        question: The user's question in their own words.
        critic_notes: Text returned by the site_critic agent (optional).
    """
    state = tool_context.state
    analysis = state.get("analysis")
    if not analysis:
        return _error("Run choose_sites first.")
    findings = state.get("red_team") or red_team(
        analysis, state.get("backtest"), state.get("transfer")
    )
    memo = build_memo(
        analysis,
        state.get("backtest"),
        state.get("transfer"),
        findings,
        critic_notes,
        list(state.get("audit", [])),
        question,
    )
    state["memo"] = memo
    return {"status": "ok", "memo_markdown": memo}


__all__ = [
    "assess_transfer_risk",
    "build_decision_memo",
    "check_data_freshness",
    "choose_sites",
    "list_cities",
    "red_team_picks",
    "run_backtest",
]
_ = np  # numpy is imported for type clarity in downstream extensions
