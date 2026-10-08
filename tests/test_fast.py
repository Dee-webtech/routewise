import inspect
import random

import pytest

from routewise import _fast, solver
from routewise.geo import distance_matrix
from routewise.solver import tour_length


def seeded_instance(n: int, seed: int):
    rng = random.Random(seed)
    points = [(6.40 + rng.random() * 0.2, 3.30 + rng.random() * 0.2) for _ in range(n)]
    matrix = distance_matrix(points)
    rest = list(range(1, n))
    rng.shuffle(rest)
    return matrix, [0, *rest]


def test_fast_two_opt_has_the_same_signature_as_the_python_version():
    fast = inspect.signature(_fast.two_opt).parameters
    slow = inspect.signature(solver.two_opt).parameters
    assert list(fast) == list(slow)
    assert [p.default for p in fast.values()] == [p.default for p in slow.values()]


def test_fast_two_opt_matches_python_length_whichever_backend_is_active():
    for seed in (1, 2, 3):
        matrix, tour = seeded_instance(60, seed)
        expected = tour_length(matrix, solver.two_opt(matrix, tour, 100))
        actual = tour_length(matrix, _fast.two_opt(matrix, tour, 100))
        assert actual == pytest.approx(expected, rel=1e-12)


def test_has_cpp_flag_is_boolean():
    assert isinstance(_fast.HAS_CPP, bool)


@pytest.mark.skipif(not _fast.HAS_CPP, reason="C++ extension not built")
def test_cpp_and_python_produce_equal_length_tours():
    for seed in (1, 2, 3, 4, 5):
        matrix, tour = seeded_instance(80, seed)
        py_tour = solver.two_opt(matrix, tour, 100)
        cpp_tour = list(_fast.two_opt_cpp(matrix, tour, 100))
        assert tour_length(matrix, cpp_tour) == pytest.approx(
            tour_length(matrix, py_tour), rel=1e-12
        )
        assert cpp_tour == py_tour
        assert sorted(cpp_tour) == list(range(80))


@pytest.mark.skipif(not _fast.HAS_CPP, reason="C++ extension not built")
def test_cpp_rejects_out_of_range_indices():
    matrix, _ = seeded_instance(6, 1)
    with pytest.raises(ValueError):
        _fast.two_opt_cpp(matrix, [0, 1, 99, 2, 3, 4], 100)
