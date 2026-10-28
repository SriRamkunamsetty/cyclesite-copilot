import numpy as np
import pytest

from cyclesite.model import fit_demand_model
from cyclesite.problem import build_problem
from cyclesite.repository import CachingRepository
from cyclesite.synthetic import SyntheticRepository, make_city, synthetic_city_ids


def test_synthetic_cities_are_deterministic_and_labelled():
    a, b = make_city("demo_a"), make_city("demo_a")
    assert np.array_equal(a.points.lat, b.points.lat)
    assert a.source == "synthetic" and "SYNTHETIC" in a.notes[0]
    assert set(synthetic_city_ids()) == {"demo_a", "demo_b", "demo_c"}
    with pytest.raises(KeyError):
        make_city("nowhere")


def test_observed_city_uses_stations_as_demand_and_cells_as_candidates():
    p = build_problem(make_city("demo_a"))
    assert p.demand.source == "observed" and p.demand.lat.size == 150
    assert p.candidates.lat.size == 22 * 22
    assert len(p.existing) == 2
    assert (p.demand.low <= p.demand.mid).all() and (p.demand.mid <= p.demand.high).all()


def test_city_without_bikeshare_needs_a_model_then_is_flagged_as_modelled():
    c = make_city("demo_c")
    assert not c.has_observed_demand
    with pytest.raises(ValueError):
        build_problem(c)
    src = make_city("demo_a").stations()
    p = build_problem(c, demand_model=fit_demand_model(src.features, src.trips))
    assert p.demand.source == "modelled" and any("MODELLED" in n for n in p.notes)


def test_demo_b_is_old_data():
    from cyclesite.freshness import assess_freshness

    assert assess_freshness(make_city("demo_b").newest_data).level == "expired"
    assert assess_freshness(make_city("demo_a").newest_data).level == "fresh"


def test_caching_repository_reuses_loaded_city():
    calls = []

    class Inner(SyntheticRepository):
        def load_city(self, city_id):
            calls.append(city_id)
            return super().load_city(city_id)

    now = [0.0]
    repo = CachingRepository(Inner(), ttl_seconds=10)
    repo._clock = lambda: now[0]
    repo.load_city("demo_a")
    repo.load_city("demo_a")
    assert calls == ["demo_a"]
    now[0] = 11.0
    repo.load_city("demo_a")
    assert calls == ["demo_a", "demo_a"]
