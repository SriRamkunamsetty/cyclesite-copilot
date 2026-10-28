"""Deterministic SYNTHETIC cities for tests and the offline demo.

Nothing here is real data. Every report built from these cities says so. The generator plants a
known relationship (demand depends on attractors *and* a little on infrastructure) so tests can
check that the backtest detects it, and a shifted city so the transfer check has something to flag.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from .repository import CityData, PointTable

_SPECS = {
    "demo_a": dict(
        name="Demo City A (synthetic bikeshare)",
        seed=11,
        centre=(40.00, -75.00),
        observed=True,
        shift=0.0,
        age_days=20,
    ),
    "demo_b": dict(
        name="Demo City B (synthetic bikeshare, old data)",
        seed=23,
        centre=(35.00, -97.00),
        observed=True,
        shift=0.15,
        age_days=2500,
    ),
    "demo_c": dict(
        name="Demo City C (synthetic, no bikeshare, unlike A and B)",
        seed=37,
        centre=(18.50, 73.85),
        observed=False,
        shift=1.6,
        age_days=40,
    ),
}
_KM_LAT = 1 / 111.32


def synthetic_city_ids() -> list[str]:
    return list(_SPECS)


def _features(rng, hub_dist_km: np.ndarray, shift: float) -> dict[str, np.ndarray]:
    n = hub_dist_km.size
    prox = np.exp(-hub_dist_km / 2.5)
    scale = 1.0 + shift
    feats = {
        "track_m": rng.gamma(1.5, 500.0, n) * (0.3 + prox) * scale,
        "lane_m": rng.gamma(1.5, 700.0, n) * (0.3 + prox) * scale,
        "road_m": 9000.0 + rng.gamma(2.0, 2500.0, n) * (0.5 + prox),
        "lit_share": np.clip(rng.beta(4, 2, n) + 0.1 * prox, 0, 1),
        "paved_share": np.clip(rng.beta(6, 2, n), 0, 1),
        "poi_food": rng.poisson(14 * prox * scale + 0.5).astype(float),
        "poi_office": rng.poisson(10 * prox * scale + 0.3).astype(float),
        "poi_edu": rng.poisson(2.5 * prox * scale + 0.1).astype(float),
        "poi_transit": rng.poisson(5 * prox * scale + 0.2).astype(float),
        "poi_leisure": rng.poisson(6 * prox * scale + 0.4).astype(float),
    }
    return feats


def _true_trips(rng, f: dict[str, np.ndarray], infra_effect: float) -> np.ndarray:
    log_t = (
        4.0
        + 0.55 * np.log1p(f["poi_food"])
        + 0.45 * np.log1p(f["poi_office"])
        + 0.30 * np.log1p(f["poi_transit"])
        + 0.25 * np.log1p(f["poi_edu"])
        + infra_effect * np.log1p(f["track_m"] / 100.0)
        + 0.75 * infra_effect * f["lit_share"]
        + rng.normal(0, 0.35, f["poi_food"].size)
    )
    return np.expm1(log_t)


def make_city(
    city_id: str,
    now: datetime | None = None,
    n_stations: int = 150,
    grid: int = 22,
    infra_effect: float = 0.5,
) -> CityData:
    if city_id not in _SPECS:
        raise KeyError(f"Unknown synthetic city '{city_id}'. Known: {synthetic_city_ids()}")
    spec = _SPECS[city_id]
    rng = np.random.default_rng(spec["seed"])
    lat0, lon0 = spec["centre"]
    km_lon = _KM_LAT / np.cos(np.radians(lat0))
    hubs = rng.normal(0, 2.5, size=(4, 2))  # km offsets

    def dist_to_hubs(xk, yk):
        return np.min(
            np.hypot(xk[:, None] - hubs[None, :, 0], yk[:, None] - hubs[None, :, 1]), axis=1
        )

    ids, kinds, lats, lons, trips_mid = [], [], [], [], []
    feats_list = []
    if spec["observed"]:
        sx, sy = rng.normal(0, 3.0, n_stations), rng.normal(0, 3.0, n_stations)
        f = _features(rng, dist_to_hubs(sx, sy), spec["shift"])
        ids += [f"S{i:03d}" for i in range(n_stations)]
        kinds += ["station"] * n_stations
        lats += list(lat0 + sy * _KM_LAT)
        lons += list(lon0 + sx * km_lon)
        trips_mid += list(_true_trips(rng, f, infra_effect))
        feats_list.append(f)
    gx, gy = np.meshgrid(np.linspace(-6, 6, grid), np.linspace(-6, 6, grid))
    gx, gy = gx.ravel(), gy.ravel()
    fc = _features(rng, dist_to_hubs(gx, gy), spec["shift"])
    n_cells = gx.size
    ids += [f"C{i:04d}" for i in range(n_cells)]
    kinds += ["cell"] * n_cells
    lats += list(lat0 + gy * _KM_LAT)
    lons += list(lon0 + gx * km_lon)
    trips_mid += [np.nan] * n_cells
    feats_list.append(fc)

    feats = {k: np.concatenate([f[k] for f in feats_list]) for k in feats_list[0]}
    mid = np.array(trips_mid, dtype=float)
    # Synthetic forecast interval: -25% / +35% around the middle case (made up, like the rest).
    pts = PointTable(
        np.array(ids),
        np.array(kinds),
        np.array(lats),
        np.array(lons),
        feats,
        mid * 0.75,
        mid,
        mid * 1.35,
    )
    ex_idx = rng.choice(np.where(np.array(kinds) == "cell")[0], 2, replace=False)
    now = now or datetime.now(UTC)
    return CityData(
        city_id=city_id,
        name=spec["name"],
        source="synthetic",
        newest_data=now - timedelta(days=spec["age_days"]),
        points=pts,
        existing_lat=np.array(lats)[ex_idx],
        existing_lon=np.array(lons)[ex_idx],
        notes=("SYNTHETIC DEMO DATA: generated, not measured.",),
    )


class SyntheticRepository:
    def list_cities(self) -> list[dict]:
        out = []
        for cid in synthetic_city_ids():
            c = make_city(cid)
            out.append(
                {
                    "city_id": cid,
                    "name": c.name,
                    "has_observed_demand": c.has_observed_demand,
                    "source": "synthetic",
                }
            )
        return out

    def load_city(self, city_id: str) -> CityData:
        return make_city(city_id)
