"""Decision memo: a deterministic, auditable write-up of exactly what was computed."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _n(x: float) -> str:
    return f"{x:,.0f}"


def build_memo(
    analysis: dict[str, Any],
    backtest: dict | None,
    transfer: dict | None,
    findings: list[dict],
    critic_notes: str,
    audit: list[dict],
    question: str = "",
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    fresh = analysis["freshness"]
    cfg = analysis["config"]
    lines = [f"# Site decision memo: {analysis['city_name']}", ""]
    lines.append(f"_Generated {now.strftime('%Y-%m-%d %H:%M UTC')}_")
    if question:
        lines += ["", f"**Question:** {question}"]
    if analysis["source"] == "synthetic":
        lines += ["", "> **SYNTHETIC DEMO DATA.** These sites are generated examples, not advice."]
    lines += [
        "",
        "## Data",
        f"- Demand: {analysis['demand_source']} ({analysis['demand_note']})",
        f"- Newest data: {fresh.get('newest_data')} ({fresh['level']}). {fresh['message']}",
    ]
    if analysis.get("existing_shops"):
        lines.append(f"- Existing shops counted before choosing: {analysis['existing_shops']}")

    lines += [
        "",
        "## Recommended sites",
        "",
        "Ranked on the cautious (low) demand case. Demand units are modelled trips per "
        "year captured, not revenue.",
        "",
        "| Rank | Site | Lat, Lon | Rideability | Extra demand low / mid / high | Overlap |",
        "|---|---|---|---|---|---|",
    ]
    for p in analysis["picks"]:
        g = p["gain_by_case"]
        lines.append(
            f"| {p['rank']} | {p['site_id']} | {p['lat']:.5f}, {p['lon']:.5f} | "
            f"{p['rideability']:.2f} | {_n(g['low'])} / {_n(g['mid'])} / {_n(g['high'])} | "
            f"{p['overlap_share']:.0%} |"
        )
    if not analysis["picks"]:
        lines.append("| - | none found | - | - | - | - |")
    caps = analysis["captured_by_case"]
    lines += [
        "",
        f"Together the sites lift weighted coverage from {analysis['baseline_coverage']:.1%} to "
        f"{analysis['final_coverage']:.1%}. Total extra demand: low {_n(caps['low'])}, "
        f"mid {_n(caps['mid'])}, high {_n(caps['high'])}.",
    ]
    for w in analysis.get("warnings", []):
        lines.append(f"- Note: {w}")

    lines += ["", "## How sure are we?", ""]
    stab = analysis.get("stability", {})
    if stab:
        lines.append(
            f"- Stability: re-ran {stab['runs']} times with different catchment sizes and demand "
            f"cases; average overlap with the base set is {stab['mean_jaccard']:.0%}."
        )
    if backtest:
        scores = {s["model"]: s for s in backtest["scores"]}
        lines.append(
            f"- Backtest ({backtest['setting'].replace('_', ' ')}, {backtest['train_city']} -> "
            f"{backtest['test_city']}, {backtest['n_test']} held-out stations): rank correlation "
            f"with real usage is {scores['poi_only']['spearman']} for places only and "
            f"{scores['poi_plus_infra']['spearman']} with infrastructure added; lift "
            f"{backtest['lift_over_poi']} (95% interval {backtest['lift_ci95'][0]} to "
            f"{backtest['lift_ci95'][1]}). {backtest['verdict']}"
        )
        for note in backtest.get("notes", []):
            lines.append(f"  - Backtest caveat: {note}")
    else:
        lines.append("- Backtest: not run in this session.")
    if transfer:
        lines.append(f"- Transfer check: {transfer['level']}. {transfer['message']}")

    lines += ["", "## What could make this wrong", ""]
    if findings:
        for f in findings:
            where = f"[{f['pick']}] " if f["pick"] else ""
            lines.append(f"- **{f['severity']}** {where}{f['finding']}")
    else:
        lines.append("- No automated red-team findings.")
    if critic_notes.strip():
        lines += [
            "",
            "### Critic's view (written by a separate AI reviewer)",
            "",
            critic_notes.strip(),
        ]

    lines += [
        "",
        "## Assumptions you can change",
        f"- Catchment e-folding distance {cfg['decay_m']:.0f} m, "
        f"cut-off {cfg['max_radius_m']:.0f} m "
        "(illustrative; lost sales fall with distance, but the right curve varies by business).",
        f"- Minimum rideability {cfg['min_rideability']}, minimum spacing "
        f"{cfg['min_separation_m']:.0f} m, rideability floor {cfg['rideability_floor']}.",
        "- Rideability weights are set by hand (tracks 40%, lanes 15%, lighting/surface 25%, "
        "network share 20%).",
        "",
        "## Audit trail",
        f"- Agent-issued queries through the BigQuery MCP server: {len(audit)}",
    ]
    for a in audit:
        gb = f"{a['estimated_bytes'] / 1e9:.2f} GB" if a.get("estimated_bytes") is not None else "?"
        status = "allowed" if a["allowed"] else "BLOCKED: " + "; ".join(a["reasons"])
        lines.append(f"  - `{a['sql_sha256']}` {a['time']} {gb} {status}")
    return "\n".join(lines) + "\n"
