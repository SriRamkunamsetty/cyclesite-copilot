from datetime import UTC, datetime, timedelta

import numpy as np

from cyclesite.freshness import assess_freshness
from cyclesite.scoring import ALL_FEATURES
from cyclesite.transfer import assess_transfer

NOW = datetime(2026, 10, 15, tzinfo=UTC)


def test_fresh_stale_expired_unknown():
    assert assess_freshness(NOW - timedelta(days=30), NOW).level == "fresh"
    assert assess_freshness(NOW - timedelta(days=500), NOW).level == "stale"
    old = assess_freshness(datetime(2018, 5, 1, tzinfo=UTC), NOW)
    assert old.level == "expired" and old.requires_acknowledgement
    assert "2018-05-01" in old.message
    assert assess_freshness(None, NOW).level == "unknown"
    assert assess_freshness(NOW + timedelta(days=3), NOW).level == "unknown"


def test_naive_datetimes_are_treated_as_utc():
    assert assess_freshness(datetime(2026, 10, 1), NOW).level == "fresh"


def _features(rng, scale=1.0, n=300):
    out = {k: rng.gamma(2.0, 500.0 * scale, n) for k in ALL_FEATURES}
    out["lit_share"] = rng.beta(4, 2, n)
    out["paved_share"] = rng.beta(6, 2, n)
    return out


def test_similar_city_passes_and_shifted_city_is_flagged():
    rng = np.random.default_rng(0)
    src = _features(rng)
    assert assess_transfer(src, _features(rng)).level == "similar"
    shifted = assess_transfer(src, _features(rng, scale=6.0))
    assert shifted.level in {"caution", "unlike"}
    assert shifted.max_shift > 0.5
    assert set(shifted.to_dict()["per_feature_shift"]) == set(ALL_FEATURES)
