"""Transfer check: does the target city look like the city the method was tested on?"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .scoring import ALL_FEATURES, design_matrix


@dataclass(frozen=True)
class TransferAssessment:
    level: str  # "similar" | "caution" | "unlike"
    max_shift: float  # largest standardised mean difference across features
    worst_feature: str
    out_of_range_share: float  # target points outside the source 1st-99th percentile range
    out_of_range_excess: float  # that share minus the source city's own share (the baseline)
    per_feature_shift: dict[str, float]
    message: str

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "max_shift": round(self.max_shift, 2),
            "worst_feature": self.worst_feature,
            "out_of_range_share": round(self.out_of_range_share, 3),
            "out_of_range_excess": round(self.out_of_range_excess, 3),
            "per_feature_shift": {k: round(v, 2) for k, v in self.per_feature_shift.items()},
            "message": self.message,
        }


def assess_transfer(
    source: dict[str, np.ndarray],
    target: dict[str, np.ndarray],
    names: tuple[str, ...] = ALL_FEATURES,
) -> TransferAssessment:
    xs = design_matrix(source, names)
    xt = design_matrix(target, names)
    pooled = np.sqrt((xs.var(axis=0) + xt.var(axis=0)) / 2.0)
    pooled[pooled < 1e-9] = 1.0
    shift = np.abs(xt.mean(axis=0) - xs.mean(axis=0)) / pooled
    lo, hi = np.quantile(xs, 0.01, axis=0), np.quantile(xs, 0.99, axis=0)
    outside = float(((xt < lo) | (xt > hi)).any(axis=1).mean())
    # Even the source city has points outside its own 1st-99th percentile band (about 18% when 10
    # features are checked at once), so judge the target by its EXCESS over that baseline.
    baseline = float(((xs < lo) | (xs > hi)).any(axis=1).mean())
    excess = max(0.0, outside - baseline)
    worst = int(np.argmax(shift))
    max_shift = float(shift[worst])
    if max_shift < 0.5 and excess < 0.15:
        level = "similar"
        msg = "Target city looks broadly like the cities the method was tested on."
    elif max_shift < 1.0 and excess < 0.4:
        level, msg = (
            "caution",
            (
                f"Target city differs noticeably on '{names[worst]}'; "
                "treat rankings as a hypothesis."
            ),
        )
    else:
        level, msg = (
            "unlike",
            (
                f"Target city is unlike the training cities (largest gap: '{names[worst]}'). "
                "Predictions are extrapolation; collect local data before committing money."
            ),
        )
    return TransferAssessment(
        level,
        max_shift,
        names[worst],
        outside,
        excess,
        {n: float(s) for n, s in zip(names, shift, strict=True)},
        msg,
    )
