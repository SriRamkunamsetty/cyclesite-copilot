"""Turn monthly station trip counts into a yearly demand estimate with a low/high range.

The range is a transparent heuristic, not a forecast: it measures month-to-month noise after
removing the city-wide seasonal pattern, and converts it to the uncertainty of a yearly total.
It does NOT cover growth, closures, weather years or policy changes, so real uncertainty is wider.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

Z_80 = 1.2816  # one-sided z for the 10th/90th percentile
MIN_REL_BAND = 0.10  # never claim better than +/-10%
MIN_ACTIVE_MONTHS = 9  # of the window; younger or closed stations are left out


@dataclass(frozen=True)
class StationDemand:
    station_id: str
    lat: float
    lon: float
    low: float
    mid: float
    high: float
    active_months: int


def build_station_demand(
    rows: list[dict[str, Any]],
    window_months: int = 12,
    min_active_months: int = MIN_ACTIVE_MONTHS,
) -> tuple[list[StationDemand], int]:
    """Return (stations, number of stations dropped for too little history).

    ``rows`` need: station_id, lat, lon, month (date), trips.
    """
    if not rows:
        return [], 0
    months = sorted({_month_key(r["month"]) for r in rows})
    if len(months) > window_months:
        months = months[-window_months:]
    month_idx = {m: i for i, m in enumerate(months)}
    city_total = np.zeros(len(months))
    by_station: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = _month_key(r["month"])
        if key not in month_idx:
            continue
        i = month_idx[key]
        n = float(r["trips"])
        city_total[i] += n
        s = by_station.setdefault(
            str(r["station_id"]), {"counts": np.zeros(len(months)), "lat": [], "lon": []}
        )
        s["counts"][i] += n
        s["lat"].append(float(r["lat"]))
        s["lon"].append(float(r["lon"]))
    if city_total.sum() <= 0:
        return [], len(by_station)
    season = city_total / city_total.mean()  # 1.0 = an average month citywide
    out: list[StationDemand] = []
    dropped = 0
    for sid, s in sorted(by_station.items()):
        counts = s["counts"]
        active = counts > 0
        k = int(active.sum())
        if k < min_active_months or k < 1:
            dropped += 1
            continue
        mid = float(counts[active].mean() * len(months))  # annualise over the window
        adj = counts[active] / season[active]
        mean_adj = float(adj.mean())
        cv = float(adj.std(ddof=1) / mean_adj) if k > 1 and mean_adj > 0 else 0.0
        rel = max(MIN_REL_BAND, Z_80 * cv / math.sqrt(k))
        out.append(
            StationDemand(
                sid,
                float(np.median(s["lat"])),
                float(np.median(s["lon"])),
                max(0.0, mid * (1.0 - rel)),
                mid,
                mid * (1.0 + rel),
                k,
            )
        )
    return out, dropped


def _month_key(m: Any) -> tuple[int, int]:
    if isinstance(m, date):
        return (m.year, m.month)
    y, mo = str(m)[:7].split("-")
    return (int(y), int(mo))
