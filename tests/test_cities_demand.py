from datetime import date

import numpy as np
import pytest

from cyclesite.cities import CitySpec, load_cities, validate_table_path
from cyclesite.demand import build_station_demand
from cyclesite.grid import make_grid


def test_packaged_cities_load_and_are_classified():
    cities = load_cities()
    assert cities["nyc"].demand_kind == "observed" and cities["pune"].demand_kind == "modelled"
    assert cities["nyc"].bikeshare.trips_table.endswith("citibike_trips")


@pytest.mark.parametrize("bad", ["a b.c.d", "p.d", "p.d.t; DROP", "p.d.`t`", ""])
def test_table_paths_are_strictly_validated(bad):
    with pytest.raises(ValueError):
        validate_table_path(bad)


def test_city_spec_rejects_bad_boxes_and_ids():
    ok = dict(city_id="abc", name="x", south=0, west=0, north=1, east=1)
    CitySpec(**ok)
    for patch in (
        {"city_id": "Bad-Id"},
        {"north": -1},
        {"north": 9},
        {"grid_spacing_m": 5},
        {"window_months": 1},
        {"city_id": "x'; DROP"},
    ):
        with pytest.raises(ValueError):
            CitySpec(**{**ok, **patch})


def test_hostile_column_names_in_config_are_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("""
cities:
  evil:
    name: Evil
    bbox: {south: 0, west: 0, north: 1, east: 1}
    bikeshare: {trips_table: a.b.c, time_column: "t; DROP TABLE x", start_id: a, start_lat: b,
                start_lon: c, end_id: d, end_lat: e, end_lon: f}
""")
    with pytest.raises(ValueError):
        load_cities(p)


NOISE = [1.6, 0.5, 1.5, 0.6, 1.4, 0.7, 1.3, 0.8, 1.5, 0.5, 1.6, 0.4]


def rows(station, counts, lat=40.0, lon=-74.0, start=(2017, 6)):
    out = []
    for i, n in enumerate(counts):
        y, m = start[0] + (start[1] - 1 + i) // 12, (start[1] - 1 + i) % 12 + 1
        out.append(
            {"station_id": station, "lat": lat, "lon": lon, "month": date(y, m, 1), "trips": n}
        )
    return out


def test_demand_band_for_steady_and_noisy_stations():
    season = [10, 12, 20, 30, 40, 50, 50, 40, 30, 20, 12, 10]
    steady = rows("A", [v * 10 for v in season])  # follows the city pattern exactly
    noisy = rows(
        "B",
        [
            v * 10 * f
            for v, f in zip(
                season, [1.6, 0.5, 1.5, 0.6, 1.4, 0.7, 1.3, 0.8, 1.5, 0.5, 1.6, 0.4], strict=True
            )
        ],
    )
    crowd = [r for k in range(8) for r in rows(f"X{k}", [v * 10 for v in season])]
    out, dropped = build_station_demand(steady + noisy + crowd)
    a, b = {s.station_id: s for s in out}["A"], {s.station_id: s for s in out}["B"]
    assert dropped == 0 and a.mid == pytest.approx(sum(v * 10 for v in season))
    rel = lambda s: (s.high - s.low) / (2 * s.mid)  # noqa: E731
    assert rel(a) == pytest.approx(0.10, abs=1e-6)  # floor: never better than +/-10%
    assert rel(b) > rel(a)
    assert a.low < a.mid < a.high


def test_short_history_stations_are_dropped_and_counted():
    full = rows("A", [100] * 12)
    young = rows("B", [100] * 4, start=(2018, 1))
    out, dropped = build_station_demand(full + young)
    assert [s.station_id for s in out] == ["A"] and dropped == 1


def test_empty_input_and_median_location():
    assert build_station_demand([]) == ([], 0)
    r = rows("A", [100] * 12)
    r[0]["lat"] = 0.0  # one bad coordinate must not move the station
    assert build_station_demand(r)[0][0].lat == pytest.approx(40.0)


def test_grid_spacing_is_about_right():
    lat, lon = make_grid(40.0, -74.0, 40.05, -73.95, 500)
    assert lat.size == lon.size > 50
    step = np.diff(np.unique(np.round(lat, 6))).mean() * 111_320
    assert step == pytest.approx(500, rel=0.01)
    assert lat.min() > 40.0 and lon.max() < -73.95
