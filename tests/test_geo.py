import math

import pytest

from routewise.geo import distance_matrix, haversine, sweep_angle


def test_haversine_zero_distance():
    assert haversine(6.5, 3.4, 6.5, 3.4) == 0.0


def test_haversine_london_paris():
    assert haversine(51.5074, -0.1278, 48.8566, 2.3522) == pytest.approx(343.5, abs=1.5)


def test_haversine_one_degree_of_longitude_on_equator():
    assert haversine(0.0, 0.0, 0.0, 1.0) == pytest.approx(111.195, abs=0.05)


def test_haversine_one_degree_of_latitude():
    assert haversine(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.195, abs=0.05)


def test_haversine_antipodal_points_are_half_the_circumference():
    assert haversine(0.0, 0.0, 0.0, 180.0) == pytest.approx(math.pi * 6371.0088, abs=0.5)


def test_haversine_is_symmetric():
    a = haversine(6.45, 3.39, 9.07, 7.40)
    b = haversine(9.07, 7.40, 6.45, 3.39)
    assert a == pytest.approx(b)


def test_distance_matrix_shape_symmetry_and_diagonal():
    points = [(6.5, 3.4), (6.6, 3.5), (6.4, 3.3), (6.55, 3.45), (6.7, 3.2), (6.45, 3.6)]
    matrix = distance_matrix(points)
    assert len(matrix) == len(points)
    for i in range(len(points)):
        assert matrix[i][i] == 0.0
        for j in range(len(points)):
            assert matrix[i][j] == matrix[j][i]
            assert matrix[i][j] == pytest.approx(haversine(*points[i], *points[j]))


def test_distance_matrix_empty():
    assert distance_matrix([]) == []


def test_sweep_angle_compass_directions():
    depot = (0.0, 0.0)
    assert sweep_angle(depot, (0.0, 0.1)) == pytest.approx(0.0)  # east
    assert sweep_angle(depot, (0.1, 0.0)) == pytest.approx(math.pi / 2)  # north
    assert sweep_angle(depot, (0.0, -0.1)) == pytest.approx(math.pi)  # west
    assert sweep_angle(depot, (-0.1, 0.0)) == pytest.approx(3 * math.pi / 2)  # south
