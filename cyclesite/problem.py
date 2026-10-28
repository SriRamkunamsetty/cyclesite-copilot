"""Turn a CityData into the arrays the optimiser needs."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .model import DemandModel, predict_trips
from .optimizer import Candidates, DemandPoints, Existing
from .repository import CityData
from .scoring import RideabilityWeights, rideability


@dataclass(frozen=True)
class Problem:
    demand: DemandPoints
    candidates: Candidates
    existing: Existing
    notes: list[str] = field(default_factory=list)


def build_problem(
    city: CityData,
    weights: RideabilityWeights | None = None,
    demand_model: DemandModel | None = None,
) -> Problem:
    pts = city.points
    ride = rideability(pts.features, weights)
    notes: list[str] = []

    cell_mask = pts.mask("cell")
    if not cell_mask.any():
        cell_mask = pts.mask("station")
        notes.append("No grid cells found; candidate sites are the station locations.")
    cands = Candidates(pts.ids[cell_mask], pts.lat[cell_mask], pts.lon[cell_mask], ride[cell_mask])

    st_mask = pts.mask("station") & np.isfinite(pts.trips_mid)
    if st_mask.any():
        mid = pts.trips_mid[st_mask].astype(np.float64)
        low = np.where(np.isfinite(pts.trips_low[st_mask]), pts.trips_low[st_mask], mid)
        high = np.where(np.isfinite(pts.trips_high[st_mask]), pts.trips_high[st_mask], mid)
        if np.allclose(low, mid) and np.allclose(high, mid):
            notes.append("No forecast interval stored: low, mid and high demand are identical.")
        demand = DemandPoints(
            pts.ids[st_mask],
            pts.lat[st_mask],
            pts.lon[st_mask],
            low,
            mid,
            high,
            ride[st_mask],
            "observed",
        )
    else:
        if demand_model is None:
            raise ValueError(
                f"City '{city.city_id}' has no observed demand; pass a demand model fitted on a "
                "bikeshare city."
            )
        feats = {k: np.asarray(v)[cell_mask] for k, v in pts.features.items()}
        low, mid, high = predict_trips(demand_model, feats)
        demand = DemandPoints(
            cands.ids, cands.lat, cands.lon, low, mid, high, cands.rideability, "modelled"
        )
        notes.append(
            "Demand is MODELLED from nearby places (cafes, offices, colleges, transit), "
            "not observed; the low/high range comes from cross-validated model error."
        )
    existing = Existing(
        np.asarray(city.existing_lat, dtype=float), np.asarray(city.existing_lon, dtype=float)
    )
    return Problem(demand, cands, existing, notes)
