import numpy as np

from cyclesite.geo import haversine_matrix


def test_known_distance_one_degree_latitude():
    d = haversine_matrix(np.array([0.0]), np.array([0.0]), np.array([1.0]), np.array([0.0]))
    assert abs(float(d[0, 0]) - 111_195) < 200


def test_symmetry_and_zero_diagonal():
    lat = np.array([40.0, 40.01, 40.02])
    lon = np.array([-75.0, -75.01, -75.02])
    d = haversine_matrix(lat, lon, lat, lon)
    assert np.allclose(d, d.T, atol=0.5)
    assert np.allclose(np.diag(d), 0.0, atol=0.5)


def test_block_boundaries_do_not_change_result():
    rng = np.random.default_rng(1)
    lat, lon = rng.uniform(10, 11, 40), rng.uniform(20, 21, 40)
    a = haversine_matrix(lat, lon, lat, lon, block=7)
    b = haversine_matrix(lat, lon, lat, lon, block=512)
    assert np.array_equal(a, b)
