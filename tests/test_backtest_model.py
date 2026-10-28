import numpy as np
import pytest

from cyclesite.backtest import (
    StationSet,
    rankdata,
    run_backtest,
    spearman,
    split_indices,
)
from cyclesite.model import fit_demand_model, predict_trips
from cyclesite.scoring import POI_FEATURES
from cyclesite.synthetic import make_city


def test_rankdata_averages_ties():
    assert list(rankdata(np.array([10, 20, 20, 30]))) == [0.0, 1.5, 1.5, 3.0]


def test_spearman_known_values():
    a = np.arange(10)
    assert spearman(a, a * 3 + 1) == pytest.approx(1.0)
    assert spearman(a, -a) == pytest.approx(-1.0)
    assert spearman(a, np.ones(10)) == 0.0


def test_split_is_disjoint_complete_and_seeded():
    tr, te = split_indices(100, 0.3, seed=1)
    assert set(tr).isdisjoint(te) and len(tr) + len(te) == 100 and len(te) == 30
    assert np.array_equal(te, split_indices(100, 0.3, seed=1)[1])


def stations(infra_effect, n=400):
    c = make_city("demo_a", n_stations=n, infra_effect=infra_effect)
    return c.stations()


def test_backtest_detects_a_planted_infrastructure_effect():
    s = stations(1.2)
    tr, te = split_indices(s.n)
    rep = run_backtest(s.subset(tr), s.subset(te), "within_city_holdout")
    assert rep.lift_ci_low > 0 and "measurable" in rep.verdict
    assert {m.name for m in rep.scores} == {"poi_only", "rideability_only", "poi_plus_infra"}


def test_backtest_does_not_overclaim_when_there_is_no_effect():
    s = stations(0.0)
    tr, te = split_indices(s.n)
    rep = run_backtest(s.subset(tr), s.subset(te), "within_city_holdout")
    assert "measurable" not in rep.verdict


def test_backtest_is_reproducible_and_serialisable():
    s = stations(0.5)
    tr, te = split_indices(s.n)
    a = run_backtest(s.subset(tr), s.subset(te), "x").to_dict()
    b = run_backtest(s.subset(tr), s.subset(te), "x").to_dict()
    assert a == b and a["n_test"] == te.size


def test_backtest_needs_enough_holdout_and_notes_small_samples():
    s = stations(0.5, n=60)
    tr, te = split_indices(s.n)
    with pytest.raises(ValueError):
        run_backtest(s.subset(tr), s.subset(te[:5]), "x")
    rep = run_backtest(s.subset(tr), s.subset(te), "x")
    assert any("held-out" in n for n in rep.notes)


def test_station_subset_keeps_alignment():
    s = stations(0.5, n=50)
    sub = s.subset(np.array([3, 7]))
    assert isinstance(sub, StationSet) and sub.n == 2
    assert sub.features["poi_food"][1] == s.features["poi_food"][7]


def test_demand_model_orders_low_mid_high_and_needs_data():
    s = stations(0.5)
    m = fit_demand_model(s.features, s.trips, POI_FEATURES)
    low, mid, high = predict_trips(m, s.features)
    assert (low <= mid + 1e-9).all() and (mid <= high + 1e-9).all()
    assert m.resid_q10 < 0 < m.resid_q90
    with pytest.raises(ValueError):
        fit_demand_model({k: v[:5] for k, v in s.features.items()}, s.trips[:5])
