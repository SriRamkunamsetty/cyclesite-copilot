"""Rideability: how pleasant and safe is it to reach a place by bike?

Rideability uses only *infrastructure* features (separated tracks, painted lanes, lighting,
surface, how much of the road network has cycle provision). Footfall attractors (cafes, offices,
colleges, transit) are kept separate because they predict *demand*, not cyclist-friendliness.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

INFRA_FEATURES = ("track_m", "lane_m", "road_m", "lit_share", "paved_share")
POI_FEATURES = ("poi_food", "poi_office", "poi_edu", "poi_transit", "poi_leisure")
ALL_FEATURES = INFRA_FEATURES + POI_FEATURES
_SHARE_FEATURES = {"lit_share", "paved_share"}


@dataclass(frozen=True)
class RideabilityWeights:
    """Weights are an explicit, tunable assumption, not a fact."""

    protected: float = 0.40  # separated cycle tracks
    painted: float = 0.15  # painted lanes
    comfort: float = 0.25  # lighting and paved surface
    network: float = 0.20  # cycle provision as a share of all roads nearby
    track_scale_m: float = 2000.0
    lane_scale_m: float = 3000.0

    def __post_init__(self) -> None:
        total = self.protected + self.painted + self.comfort + self.network
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"Rideability weights must sum to 1, got {total}")


def design_matrix(features: dict[str, np.ndarray], names: tuple[str, ...]) -> np.ndarray:
    """Stack features into a matrix; lengths and counts are log1p-transformed."""
    cols = []
    for name in names:
        col = np.asarray(features[name], dtype=np.float64)
        cols.append(col if name in _SHARE_FEATURES else np.log1p(np.maximum(col, 0.0)))
    return np.column_stack(cols)


def rideability(
    features: dict[str, np.ndarray], weights: RideabilityWeights | None = None
) -> np.ndarray:
    """Return a 0..1 rideability score per point."""
    w = weights or RideabilityWeights()
    track = np.maximum(np.asarray(features["track_m"], dtype=np.float64), 0.0)
    lane = np.maximum(np.asarray(features["lane_m"], dtype=np.float64), 0.0)
    road = np.maximum(np.asarray(features["road_m"], dtype=np.float64), 1.0)
    lit = np.clip(np.asarray(features["lit_share"], dtype=np.float64), 0.0, 1.0)
    paved = np.clip(np.asarray(features["paved_share"], dtype=np.float64), 0.0, 1.0)

    protected = 1.0 - np.exp(-track / w.track_scale_m)
    painted = 1.0 - np.exp(-lane / w.lane_scale_m)
    comfort = 0.5 * lit + 0.5 * paved
    network = np.sqrt(np.clip((track + lane) / road, 0.0, 1.0))
    score = (
        w.protected * protected + w.painted * painted + w.comfort * comfort + w.network * network
    )
    return np.clip(score, 0.0, 1.0)
