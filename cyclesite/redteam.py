"""Deterministic red team: concrete, number-backed reasons each pick could fail.

The critic *agent* turns these findings into plain-language failure scenarios. Keeping the
findings in code means the critic cannot invent evidence.
"""

from __future__ import annotations

from typing import Any


def red_team(
    analysis: dict[str, Any], backtest: dict | None = None, transfer: dict | None = None
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    def add(severity: str, pick: str | None, text: str) -> None:
        findings.append({"severity": severity, "pick": pick, "finding": text})

    cfg = analysis.get("config", {})
    picks = analysis.get("picks", [])
    stability = analysis.get("stability", {}).get("pick_frequency", {})
    fresh = analysis.get("freshness", {})

    if fresh.get("level") in {"expired", "unknown"}:
        add("high", None, f"Data freshness: {fresh.get('message')}")
    elif fresh.get("level") == "stale":
        add("medium", None, f"Data freshness: {fresh.get('message')}")
    if len(picks) < cfg.get("k", len(picks)):
        add(
            "medium",
            None,
            f"Only {len(picks)} of {cfg.get('k')} requested sites were found; "
            "the rules left no further candidate that adds demand.",
        )
    if analysis.get("demand_source") == "modelled":
        add("high", None, "Demand is modelled from nearby places, not observed trips.")
    caps = analysis.get("captured_by_case", {})
    if caps.get("high", 0) > 0 and caps.get("low", 0) / caps["high"] < 0.6:
        add(
            "medium",
            None,
            "The low-case outcome is under 60% of the high case: the forecast "
            "range is wide, so the payoff is uncertain.",
        )
    if transfer and transfer.get("level") in {"caution", "unlike"}:
        sev = "high" if transfer["level"] == "unlike" else "medium"
        add(sev, None, f"Transfer check: {transfer.get('message')}")
    if backtest and "No reliable lift" in backtest.get("verdict", ""):
        add(
            "medium",
            None,
            "Backtest found no reliable lift from infrastructure features, so "
            "the rideability weighting is an assumption, not a finding.",
        )

    first_gain = picks[0]["marginal_gain"] if picks else 0.0
    for p in picks:
        pid = p["site_id"]
        if p["overlap_share"] > 0.25:
            add(
                "medium",
                pid,
                f"{p['overlap_share']:.0%} of this site's catchment overlaps "
                "shops already open or chosen earlier (cannibalisation).",
            )
        freq = stability.get(pid)
        if freq is not None and freq < 0.5:
            add(
                "high" if freq < 0.25 else "medium",
                pid,
                f"Chosen in only {freq:.0%} of sensitivity runs (different catchment sizes and "
                "demand cases); the pick is fragile.",
            )
        if p["rideability"] - cfg.get("min_rideability", 0.0) < 0.1:
            add(
                "low",
                pid,
                f"Rideability {p['rideability']:.2f} only just clears the "
                f"{cfg.get('min_rideability')} floor.",
            )
        if first_gain > 0 and p["rank"] > 1 and p["marginal_gain"] < 0.5 * first_gain:
            add(
                "low",
                pid,
                "Less than half the extra demand of the first pick: diminishing "
                "returns; consider whether this site is worth a lease.",
            )
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(findings, key=lambda f: order[f["severity"]])
