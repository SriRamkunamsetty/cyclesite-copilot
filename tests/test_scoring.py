import numpy as np
import pytest

from cyclesite.scoring import RideabilityWeights, design_matrix, rideability


def feats(**over):
    base = {
        "track_m": 0.0,
        "lane_m": 0.0,
        "road_m": 10000.0,
        "lit_share": 0.0,
        "paved_share": 0.0,
        "poi_food": 0,
        "poi_office": 0,
        "poi_edu": 0,
        "poi_transit": 0,
        "poi_leisure": 0,
    }
    base.update(over)
    return {k: np.array([v], dtype=float) for k, v in base.items()}


def test_bounds_and_monotonicity():
    assert rideability(feats())[0] == pytest.approx(0.0)
    low = rideability(feats(track_m=500))[0]
    high = rideability(feats(track_m=5000))[0]
    assert 0 < low < high <= 1


def test_every_infrastructure_input_helps():
    base = rideability(feats())[0]
    for key, val in {"track_m": 1500, "lane_m": 1500, "lit_share": 1.0, "paved_share": 1.0}.items():
        assert rideability(feats(**{key: val}))[0] > base, key


def test_pois_do_not_change_rideability():
    assert rideability(feats())[0] == rideability(feats(poi_food=50, poi_office=50))[0]


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        RideabilityWeights(protected=0.5)


def test_protected_track_beats_same_length_of_paint():
    track = rideability(feats(track_m=2000))[0]
    lane = rideability(feats(lane_m=2000))[0]
    assert track > lane


def test_design_matrix_log_scales_counts_but_not_shares():
    x = design_matrix(feats(track_m=99, lit_share=0.5), ("track_m", "lit_share"))
    assert x[0, 0] == pytest.approx(np.log1p(99))
    assert x[0, 1] == pytest.approx(0.5)
