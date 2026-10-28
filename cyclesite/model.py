"""A small, transparent demand model: ridge regression on log trips.

Used in two places: (1) the backtest, and (2) filling in demand for a city that has no bikeshare
data, using only footfall-attractor (POI) features so rideability is not double counted.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .scoring import POI_FEATURES, design_matrix


@dataclass(frozen=True)
class DemandModel:
    names: tuple[str, ...]
    mean: np.ndarray
    std: np.ndarray
    coef: np.ndarray
    intercept: float
    resid_q10: float  # cross-validated log-residual quantiles -> low/high demand
    resid_q90: float
    n_train: int


def _fit(x: np.ndarray, y: np.ndarray, ridge: float):
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std < 1e-9] = 1.0
    z = (x - mean) / std
    y_mean = float(y.mean())
    a = z.T @ z + ridge * np.eye(z.shape[1])
    coef = np.linalg.solve(a, z.T @ (y - y_mean))
    return mean, std, coef, y_mean


def _predict_log(x, mean, std, coef, intercept) -> np.ndarray:
    return ((x - mean) / std) @ coef + intercept


def cv_residuals(x: np.ndarray, y: np.ndarray, ridge: float, folds: int = 5, seed: int = 0):
    """Out-of-fold residuals so the reported uncertainty is not in-sample optimism."""
    rng = np.random.default_rng(seed)
    idx = rng.permutation(y.size)
    res = np.empty_like(y, dtype=np.float64)
    for f in range(folds):
        test = idx[f::folds]
        train = np.setdiff1d(idx, test)
        m = _fit(x[train], y[train], ridge)
        res[test] = y[test] - _predict_log(x[test], *m)
    return res


def fit_demand_model(
    features: dict[str, np.ndarray],
    trips: np.ndarray,
    names: tuple[str, ...] = POI_FEATURES,
    ridge: float = 1.0,
) -> DemandModel:
    trips = np.asarray(trips, dtype=np.float64)
    ok = np.isfinite(trips) & (trips >= 0)
    if ok.sum() < 20:
        raise ValueError(f"Need at least 20 stations with observed trips, got {int(ok.sum())}.")
    x = design_matrix({k: np.asarray(v)[ok] for k, v in features.items()}, names)
    y = np.log1p(trips[ok])
    mean, std, coef, intercept = _fit(x, y, ridge)
    res = cv_residuals(x, y, ridge)
    return DemandModel(
        names,
        mean,
        std,
        coef,
        intercept,
        float(np.quantile(res, 0.10)),
        float(np.quantile(res, 0.90)),
        int(ok.sum()),
    )


def predict_log(model: DemandModel, features: dict[str, np.ndarray]) -> np.ndarray:
    x = design_matrix(features, model.names)
    return _predict_log(x, model.mean, model.std, model.coef, model.intercept)


def predict_trips(model: DemandModel, features: dict[str, np.ndarray]):
    """Return (low, mid, high) trips per year."""
    p = predict_log(model, features)
    low = np.expm1(p + model.resid_q10)
    mid = np.expm1(p)
    high = np.expm1(p + model.resid_q90)
    return tuple(np.maximum(a, 0.0) for a in (low, mid, high))
