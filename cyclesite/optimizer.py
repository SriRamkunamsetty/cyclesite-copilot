"""Portfolio optimiser: choose K shop sites that maximise *extra* captured demand.

gain(j | S) = sum_c D_c * max(0, f(d_cj) - max_{s in S} f(d_cs))

D_c is the (rideability-weighted) demand at point c, f is a distance-decay catchment and S is
the set of shops already open or already chosen. Existing shops start inside S, which is how
cannibalisation enters. Greedy selection is the standard (1 - 1/e)-approximation for this
monotone submodular coverage objective.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from .geo import haversine_matrix

Case = Literal["low", "mid", "high"]
CASES: tuple[Case, ...] = ("low", "mid", "high")


@dataclass(frozen=True)
class DemandPoints:
    """Where demand is observed or modelled, with a low/mid/high range."""

    ids: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    low: np.ndarray
    mid: np.ndarray
    high: np.ndarray
    rideability: np.ndarray
    source: str = "observed"  # "observed" (e.g. bikeshare trips) or "modelled"

    def case(self, name: Case) -> np.ndarray:
        return {"low": self.low, "mid": self.mid, "high": self.high}[name]


@dataclass(frozen=True)
class Candidates:
    ids: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    rideability: np.ndarray


@dataclass(frozen=True)
class Existing:
    lat: np.ndarray
    lon: np.ndarray

    @staticmethod
    def none() -> Existing:
        return Existing(np.array([]), np.array([]))

    def __len__(self) -> int:
        return int(self.lat.size)


@dataclass(frozen=True)
class OptimizerConfig:
    k: int = 3
    decay_m: float = 900.0  # e-folding distance of the catchment; a tunable assumption
    max_radius_m: float = 3000.0
    min_rideability: float = 0.35
    rideability_floor: float = 0.30  # weight a zero-rideability cell keeps (0..1)
    min_separation_m: float = 400.0
    demand_case: Case = "low"  # rank on the cautious case by default

    def __post_init__(self) -> None:
        if self.k < 1 or self.k > 50:
            raise ValueError("k must be between 1 and 50")
        if self.decay_m <= 0 or self.max_radius_m <= 0:
            raise ValueError("decay_m and max_radius_m must be positive")
        if not 0.0 <= self.rideability_floor <= 1.0:
            raise ValueError("rideability_floor must be within [0, 1]")
        if not 0.0 <= self.min_rideability <= 1.0:
            raise ValueError("min_rideability must be within [0, 1]")


@dataclass(frozen=True)
class Pick:
    rank: int
    site_id: str
    lat: float
    lon: float
    rideability: float
    marginal_gain: float  # extra captured demand given existing + earlier picks
    standalone_gain: float  # gain if only existing shops were open
    overlap_share: float  # share of standalone gain lost to overlap (cannibalisation)
    coverage_after: float  # share of total weighted demand captured after this pick
    gain_by_case: dict[str, float] = field(default_factory=dict)  # this pick's gain: low/mid/high


@dataclass(frozen=True)
class SelectionResult:
    picks: list[Pick]
    total_weighted_demand: float
    baseline_coverage: float
    final_coverage: float
    captured_by_case: dict[str, float]  # extra captured demand of the chosen set per case
    config: OptimizerConfig
    warnings: list[str] = field(default_factory=list)


def catchment(d: np.ndarray, decay_m: float, max_radius_m: float) -> np.ndarray:
    f = np.exp(-d / decay_m).astype(np.float32)
    f[d > max_radius_m] = 0.0
    return f


def weighted_demand(demand: DemandPoints, case: Case, floor: float) -> np.ndarray:
    base = np.maximum(demand.case(case).astype(np.float64), 0.0)
    return base * (floor + (1.0 - floor) * np.clip(demand.rideability, 0.0, 1.0))


class DistanceCache:
    """Distances are expensive; compute once and reuse across decay/case sensitivity runs."""

    def __init__(self, demand: DemandPoints, cands: Candidates, existing: Existing):
        self.d_dc = haversine_matrix(demand.lat, demand.lon, cands.lat, cands.lon)
        self.d_cc_cache: dict[int, np.ndarray] = {}
        self.d_de = (
            haversine_matrix(demand.lat, demand.lon, existing.lat, existing.lon)
            if len(existing)
            else np.zeros((demand.lat.size, 0), dtype=np.float32)
        )
        self.d_ce = (
            haversine_matrix(cands.lat, cands.lon, existing.lat, existing.lon)
            if len(existing)
            else np.zeros((cands.lat.size, 0), dtype=np.float32)
        )
        self._cands = cands

    def cand_to_all(self, j: int) -> np.ndarray:
        if j not in self.d_cc_cache:
            c = self._cands
            self.d_cc_cache[j] = haversine_matrix(c.lat[j : j + 1], c.lon[j : j + 1], c.lat, c.lon)[
                0
            ]
        return self.d_cc_cache[j]


def _greedy(
    demand: DemandPoints,
    cands: Candidates,
    cache: DistanceCache,
    cfg: OptimizerConfig,
    case: Case,
) -> tuple[list[int], list[dict], float, float, list[str]]:
    w = weighted_demand(demand, case, cfg.rideability_floor)
    total = float(w.sum())
    warnings: list[str] = []
    if total <= 0:
        return [], [], 0.0, 0.0, ["Total demand is zero; nothing to optimise."]

    f_dc = catchment(cache.d_dc, cfg.decay_m, cfg.max_radius_m)
    cover = np.zeros(w.size, dtype=np.float32)
    if cache.d_de.shape[1]:
        cover = catchment(cache.d_de, cfg.decay_m, cfg.max_radius_m).max(axis=1)
    baseline = float((w * cover).sum() / total)

    eligible = cands.rideability >= cfg.min_rideability
    if cache.d_ce.shape[1]:
        eligible &= cache.d_ce.min(axis=1) >= cfg.min_separation_m
    if not eligible.any():
        return [], [], baseline, baseline, ["No candidate meets the rideability/separation rules."]

    standalone = w.astype(np.float32) @ np.maximum(f_dc - cover[:, None], 0.0)
    chosen: list[int] = []
    details: list[dict] = []
    for _ in range(cfg.k):
        gains = w.astype(np.float32) @ np.maximum(f_dc - cover[:, None], 0.0)
        gains = np.where(eligible, gains, -np.inf)
        j = int(np.argmax(gains))
        gain = float(gains[j])
        if not np.isfinite(gain) or gain <= 1e-9:
            warnings.append(
                f"Stopped at {len(chosen)} site(s): no remaining candidate adds demand."
            )
            break
        cover = np.maximum(cover, f_dc[:, j])
        eligible &= cache.cand_to_all(j) >= cfg.min_separation_m
        chosen.append(j)
        alone = float(standalone[j])
        details.append(
            {
                "j": j,
                "gain": gain,
                "standalone": alone,
                "overlap": float(max(0.0, 1.0 - gain / alone)) if alone > 0 else 0.0,
                "coverage_after": float((w * cover).sum() / total),
            }
        )
    final = float((w * cover).sum() / total)
    return chosen, details, baseline, final, warnings


def evaluate_set(
    demand: DemandPoints,
    cands: Candidates,
    chosen: list[int],
    cache: DistanceCache,
    cfg: OptimizerConfig,
    case: Case,
) -> float:
    """Extra weighted demand captured by ``chosen`` under one demand case."""
    w = weighted_demand(demand, case, cfg.rideability_floor)
    f_dc = catchment(cache.d_dc, cfg.decay_m, cfg.max_radius_m)
    base = np.zeros(w.size, dtype=np.float32)
    if cache.d_de.shape[1]:
        base = catchment(cache.d_de, cfg.decay_m, cfg.max_radius_m).max(axis=1)
    cover = base.copy()
    for j in chosen:
        cover = np.maximum(cover, f_dc[:, j])
    return float((w * (cover - base)).sum())


def select_sites(
    demand: DemandPoints,
    cands: Candidates,
    existing: Existing | None,
    cfg: OptimizerConfig,
    cache: DistanceCache | None = None,
) -> SelectionResult:
    existing = existing or Existing.none()
    if demand.lat.size == 0 or cands.lat.size == 0:
        raise ValueError("Need at least one demand point and one candidate site.")
    cache = cache or DistanceCache(demand, cands, existing)
    chosen, details, baseline, final, warnings = _greedy(demand, cands, cache, cfg, cfg.demand_case)
    prefix = {
        c: [0.0]
        + [evaluate_set(demand, cands, chosen[: i + 1], cache, cfg, c) for i in range(len(chosen))]
        for c in CASES
    }
    picks = [
        Pick(
            rank=i + 1,
            site_id=str(cands.ids[d["j"]]),
            lat=float(cands.lat[d["j"]]),
            lon=float(cands.lon[d["j"]]),
            rideability=float(cands.rideability[d["j"]]),
            marginal_gain=d["gain"],
            standalone_gain=d["standalone"],
            overlap_share=d["overlap"],
            coverage_after=d["coverage_after"],
            gain_by_case={c: prefix[c][i + 1] - prefix[c][i] for c in CASES},
        )
        for i, d in enumerate(details)
    ]
    captured = {c: evaluate_set(demand, cands, chosen, cache, cfg, c) for c in CASES}
    total = float(weighted_demand(demand, cfg.demand_case, cfg.rideability_floor).sum())
    return SelectionResult(picks, total, baseline, final, captured, cfg, warnings)


@dataclass(frozen=True)
class StabilityReport:
    runs: int
    pick_frequency: dict[str, float]  # site_id -> share of sensitivity runs that chose it
    mean_jaccard: float  # similarity of each run's set to the base set (1.0 = identical)
    base_ids: list[str]


def stability_report(
    demand: DemandPoints,
    cands: Candidates,
    existing: Existing | None,
    cfg: OptimizerConfig,
    decay_multipliers: tuple[float, ...] = (0.6, 0.8, 1.0, 1.25, 1.5),
    cache: DistanceCache | None = None,
) -> StabilityReport:
    """Re-run the optimiser under different decay assumptions and demand cases."""
    existing = existing or Existing.none()
    cache = cache or DistanceCache(demand, cands, existing)
    base = select_sites(demand, cands, existing, cfg, cache)
    base_ids = [p.site_id for p in base.picks]
    counts: dict[str, int] = {}
    jaccards: list[float] = []
    runs = 0
    for mult in decay_multipliers:
        for case in CASES:
            c = OptimizerConfig(
                k=cfg.k,
                decay_m=cfg.decay_m * mult,
                max_radius_m=cfg.max_radius_m,
                min_rideability=cfg.min_rideability,
                rideability_floor=cfg.rideability_floor,
                min_separation_m=cfg.min_separation_m,
                demand_case=case,
            )
            chosen, *_ = _greedy(demand, cands, cache, c, case)
            ids = [str(cands.ids[j]) for j in chosen]
            runs += 1
            for i in ids:
                counts[i] = counts.get(i, 0) + 1
            union = set(ids) | set(base_ids)
            jaccards.append(len(set(ids) & set(base_ids)) / len(union) if union else 1.0)
    freq = {i: counts.get(i, 0) / runs for i in base_ids}
    return StabilityReport(runs, freq, float(np.mean(jaccards)), base_ids)
