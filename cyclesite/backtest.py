"""Backtest: does the score predict real station usage on stations it has not seen?

Three models are compared on held-out stations:
  A. poi_only          ridge on footfall attractors (what a trip-count agent implicitly assumes)
  B. rideability_only  the hand-built rideability index, no fitting
  C. poi_plus_infra    ridge on attractors plus infrastructure features
The headline number is the paired-bootstrap lift of C over A. If its interval includes zero we
say so: rideability is then a design assumption, not an evidence-backed driver.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .model import DemandModel, fit_demand_model, predict_log
from .scoring import ALL_FEATURES, POI_FEATURES, RideabilityWeights, rideability


def rankdata(a: np.ndarray) -> np.ndarray:
    """Average ranks (ties share the mean rank)."""
    a = np.asarray(a, dtype=np.float64)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(a.size, dtype=np.float64)
    ranks[order] = np.arange(a.size, dtype=np.float64)
    sorted_a = a[order]
    i = 0
    while i < a.size:
        j = i
        while j + 1 < a.size and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        if j > i:
            ranks[order[i : j + 1]] = (i + j) / 2.0
        i = j + 1
    return ranks


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = rankdata(a), rankdata(b)
    ra -= ra.mean()
    rb -= rb.mean()
    denom = float(np.sqrt((ra**2).sum() * (rb**2).sum()))
    return float((ra * rb).sum() / denom) if denom > 0 else 0.0


@dataclass(frozen=True)
class StationSet:
    features: dict[str, np.ndarray]
    trips: np.ndarray
    city_id: str = ""

    def subset(self, idx: np.ndarray) -> StationSet:
        return StationSet(
            {k: np.asarray(v)[idx] for k, v in self.features.items()},
            np.asarray(self.trips)[idx],
            self.city_id,
        )

    @property
    def n(self) -> int:
        return int(np.asarray(self.trips).size)


def split_indices(n: int, test_frac: float = 0.3, seed: int = 0):
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    n_test = max(1, int(round(n * test_frac)))
    return np.sort(idx[n_test:]), np.sort(idx[:n_test])


@dataclass(frozen=True)
class ModelScore:
    name: str
    spearman: float
    ci_low: float
    ci_high: float


@dataclass(frozen=True)
class BacktestReport:
    setting: str  # "within_city_holdout" or "cross_city"
    train_city: str
    test_city: str
    n_train: int
    n_test: int
    scores: list[ModelScore]
    lift_over_poi: float
    lift_ci_low: float
    lift_ci_high: float
    verdict: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "setting": self.setting,
            "train_city": self.train_city,
            "test_city": self.test_city,
            "n_train": self.n_train,
            "n_test": self.n_test,
            "scores": [
                {
                    "model": s.name,
                    "spearman": round(float(s.spearman), 3),
                    "ci95": [round(float(s.ci_low), 3), round(float(s.ci_high), 3)],
                }
                for s in self.scores
            ],
            "lift_over_poi": round(float(self.lift_over_poi), 3),
            "lift_ci95": [round(float(self.lift_ci_low), 3), round(float(self.lift_ci_high), 3)],
            "verdict": self.verdict,
            "notes": self.notes,
        }


def _bootstrap(fn, n: int, n_boot: int, rng: np.random.Generator) -> np.ndarray:
    out = np.empty(n_boot)
    for i in range(n_boot):
        out[i] = fn(rng.integers(0, n, n))
    return out


def run_backtest(
    train: StationSet,
    test: StationSet,
    setting: str,
    weights: RideabilityWeights | None = None,
    n_boot: int = 400,
    seed: int = 0,
) -> BacktestReport:
    if test.n < 8:
        raise ValueError(f"Need at least 8 held-out stations to backtest, got {test.n}.")
    model_a: DemandModel = fit_demand_model(train.features, train.trips, POI_FEATURES)
    model_c: DemandModel = fit_demand_model(train.features, train.trips, ALL_FEATURES)
    y = np.asarray(test.trips, dtype=np.float64)
    pred_a = predict_log(model_a, test.features)
    pred_c = predict_log(model_c, test.features)
    pred_b = rideability(test.features, weights)

    rng = np.random.default_rng(seed)
    n = y.size
    scores = []
    for name, pred in (
        ("poi_only", pred_a),
        ("rideability_only", pred_b),
        ("poi_plus_infra", pred_c),
    ):
        dist = _bootstrap(lambda ix, p=pred: spearman(p[ix], y[ix]), n, n_boot, rng)
        lo_s, hi_s = (float(q) for q in np.quantile(dist, [0.025, 0.975]))
        scores.append(ModelScore(name, spearman(pred, y), lo_s, hi_s))
    diff = _bootstrap(
        lambda ix: spearman(pred_c[ix], y[ix]) - spearman(pred_a[ix], y[ix]), n, n_boot, rng
    )
    lift = spearman(pred_c, y) - spearman(pred_a, y)
    lo, hi = (float(q) for q in np.quantile(diff, [0.025, 0.975]))
    if lo > 0:
        verdict = "Infrastructure features add measurable predictive lift on held-out stations."
    elif hi < 0:
        verdict = "Infrastructure features made predictions worse on held-out stations."
    else:
        verdict = (
            "No reliable lift: the interval includes zero, so rideability is a design "
            "assumption here, not an evidence-backed driver."
        )
    notes = []
    if test.n < 30:
        notes.append(f"Only {test.n} held-out stations; intervals are wide.")
    return BacktestReport(
        setting, train.city_id, test.city_id, train.n, test.n, scores, lift, lo, hi, verdict, notes
    )
