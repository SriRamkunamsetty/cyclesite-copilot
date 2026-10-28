import numpy as np
import pytest

from cyclesite.optimizer import (
    CASES,
    DistanceCache,
    Existing,
    OptimizerConfig,
    catchment,
    evaluate_set,
    select_sites,
    stability_report,
)

from .helpers import cands, demand, existing

# Cluster A near (40.00, -75.00); cluster B ~11 km north. Catchments (<= 3 km) never overlap.
A = (40.000, -75.000)
B = (40.100, -75.000)


def two_clusters(weight_a=100.0, weight_b=60.0, ride=None):
    d = demand(
        [A[0], A[0] + 0.002, B[0], B[0] + 0.002],
        [A[1]] * 4,
        [weight_a, weight_a, weight_b, weight_b],
        spread=0.2,
    )
    c = cands([A[0], B[0], A[0] + 0.05], [A[1], B[1], A[1]], ride)
    return d, c


def test_catchment_decays_and_cuts_off():
    f = catchment(np.array([0.0, 900.0, 5000.0]), 900.0, 3000.0)
    assert f[0] == 1.0 and 0.36 < f[1] < 0.37 and f[2] == 0.0


def test_picks_the_heavier_cluster_first():
    d, c = two_clusters()
    r = select_sites(d, c, None, OptimizerConfig(k=1))
    assert r.picks[0].site_id == "C0"


def test_k2_covers_both_clusters_and_gains_do_not_increase():
    d, c = two_clusters()
    r = select_sites(d, c, None, OptimizerConfig(k=2))
    assert [p.site_id for p in r.picks] == ["C0", "C1"]
    assert r.picks[0].marginal_gain >= r.picks[1].marginal_gain
    assert r.final_coverage > r.baseline_coverage


def test_existing_shop_pushes_pick_to_uncovered_cluster():
    d, c = two_clusters()
    r = select_sites(d, c, existing([A[0]], [A[1]]), OptimizerConfig(k=1, min_separation_m=0))
    assert r.picks[0].site_id == "C1"


def test_cannibalisation_is_reported_when_picks_overlap():
    d = demand([40.0, 40.0], [-75.0, -74.99], [100, 100])
    c = cands([40.0, 40.0], [-75.0, -74.99])
    r = select_sites(d, c, None, OptimizerConfig(k=2, min_separation_m=0))
    assert r.picks[1].overlap_share > 0.2
    assert r.picks[1].marginal_gain < r.picks[1].standalone_gain


def test_min_separation_blocks_adjacent_sites():
    d = demand([40.0, 40.0], [-75.0, -74.999], [100, 100])
    c = cands([40.0, 40.0], [-75.0, -74.999])
    r = select_sites(d, c, None, OptimizerConfig(k=2, min_separation_m=400))
    assert len(r.picks) == 1
    assert any("Stopped" in w for w in r.warnings)


def test_low_rideability_candidate_is_never_chosen():
    d, c = two_clusters(ride=[0.1, 0.9, 0.9])
    r = select_sites(d, c, None, OptimizerConfig(k=2, min_rideability=0.35))
    assert "C0" not in [p.site_id for p in r.picks]


def test_no_eligible_candidate_returns_warning_not_crash():
    d, c = two_clusters(ride=[0.0, 0.0, 0.0])
    r = select_sites(d, c, None, OptimizerConfig(k=2))
    assert r.picks == [] and r.warnings


def test_cases_are_ordered_low_mid_high():
    d, c = two_clusters()
    r = select_sites(d, c, None, OptimizerConfig(k=2))
    assert r.captured_by_case["low"] < r.captured_by_case["mid"] < r.captured_by_case["high"]
    for p in r.picks:
        assert p.gain_by_case["low"] <= p.gain_by_case["mid"] <= p.gain_by_case["high"]


def test_per_pick_gains_sum_to_captured_total():
    d, c = two_clusters()
    r = select_sites(d, c, None, OptimizerConfig(k=2, demand_case="mid"))
    assert sum(p.gain_by_case["mid"] for p in r.picks) == pytest.approx(r.captured_by_case["mid"])


def test_greedy_matches_brute_force_within_guarantee():
    rng = np.random.default_rng(3)
    n = 25
    d = demand(40 + rng.uniform(0, 0.05, n), -75 + rng.uniform(0, 0.05, n), rng.uniform(10, 100, n))
    c = cands(40 + rng.uniform(0, 0.05, 9), -75 + rng.uniform(0, 0.05, 9))
    cfg = OptimizerConfig(k=3, min_separation_m=0, demand_case="mid", min_rideability=0.0)
    cache = DistanceCache(d, c, Existing.none())
    greedy = select_sites(d, c, None, cfg, cache).captured_by_case["mid"]
    from itertools import combinations

    best = max(evaluate_set(d, c, list(s), cache, cfg, "mid") for s in combinations(range(9), 3))
    assert greedy >= (1 - 1 / np.e) * best - 1e-6
    assert greedy <= best + 1e-6


def test_config_validation():
    for bad in (
        {"k": 0},
        {"k": 51},
        {"decay_m": 0},
        {"rideability_floor": 2},
        {"min_rideability": -1},
    ):
        with pytest.raises(ValueError):
            OptimizerConfig(**bad)


def test_empty_inputs_raise():
    d, c = two_clusters()
    with pytest.raises(ValueError):
        select_sites(d, cands([], []), None, OptimizerConfig())


def test_stability_report_covers_all_runs_and_is_bounded():
    d, c = two_clusters()
    rep = stability_report(d, c, None, OptimizerConfig(k=2))
    assert rep.runs == 5 * len(CASES)
    assert all(0 <= v <= 1 for v in rep.pick_frequency.values())
    assert 0 <= rep.mean_jaccard <= 1
    assert rep.base_ids == ["C0", "C1"]
