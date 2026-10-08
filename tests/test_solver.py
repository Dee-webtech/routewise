import random

import pytest

from routewise.geo import distance_matrix
from routewise.solver import (
    SolverError,
    baseline_distance,
    improve_tour,
    nearest_neighbour,
    or_opt,
    solve_vrp,
    sweep_assign,
    tour_length,
    two_opt,
)

DEPOT = (6.5, 3.4)


def random_stops(n: int, seed: int):
    rng = random.Random(seed)
    return [(6.40 + rng.random() * 0.2, 3.30 + rng.random() * 0.2) for _ in range(n)]


def make_instance(n: int, seed: int):
    stops = random_stops(n, seed)
    matrix = distance_matrix([DEPOT, *stops])
    return stops, matrix


def shuffled_tour(size: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    rest = list(range(1, size))
    rng.shuffle(rest)
    return [0, *rest]


def route_length(matrix, route: list[int]) -> float:
    """Length of depot -> stops (0-based stop indices) -> depot."""
    return tour_length(matrix, [0, *[s + 1 for s in route]])


def test_nearest_neighbour_visits_every_index_exactly_once():
    for n in (1, 2, 5, 30, 100):
        _, matrix = make_instance(n, seed=n)
        tour = nearest_neighbour(matrix)
        assert tour[0] == 0
        assert sorted(tour) == list(range(n + 1))


def test_nearest_neighbour_is_greedy_on_a_line():
    positions = [0, 3, 1, 2]
    matrix = [[float(abs(a - b)) for b in positions] for a in positions]
    assert nearest_neighbour(matrix) == [0, 2, 3, 1]


def test_two_opt_never_increases_length():
    for seed in range(15):
        size = 6 + seed * 3
        _, matrix = make_instance(size - 1, seed=seed)
        tour = shuffled_tour(size, seed)
        original = list(tour)
        before = tour_length(matrix, tour)
        result = two_opt(matrix, tour)
        assert tour == original  # input is not mutated
        assert result[0] == 0
        assert sorted(result) == list(range(size))
        assert tour_length(matrix, result) <= before + 1e-9


def test_two_opt_removes_a_crossing():
    points = [(0.0, 0.0), (0.0, 0.1), (0.1, 0.1), (0.1, 0.0)]
    matrix = distance_matrix(points)
    crossed = [0, 2, 1, 3]
    result = two_opt(matrix, crossed)
    assert result == [0, 1, 2, 3]
    assert tour_length(matrix, result) < tour_length(matrix, crossed)


def test_two_opt_with_zero_passes_changes_nothing():
    _, matrix = make_instance(12, seed=4)
    tour = shuffled_tour(13, seed=4)
    assert two_opt(matrix, tour, 0) == tour


def test_or_opt_never_increases_length_and_keeps_all_stops():
    for seed in range(15):
        size = 6 + seed * 3
        _, matrix = make_instance(size - 1, seed=seed + 100)
        tour = shuffled_tour(size, seed)
        before = tour_length(matrix, tour)
        result = or_opt(matrix, tour)
        assert result[0] == 0
        assert sorted(result) == list(range(size))
        assert tour_length(matrix, result) <= before + 1e-9


def test_or_opt_relocates_a_misplaced_stop():
    positions = [0, 1, 2, 3, 4]
    matrix = [[float(abs(a - b)) for b in positions] for a in positions]
    result = or_opt(matrix, [0, 1, 3, 2, 4])
    assert tour_length(matrix, result) == pytest.approx(8.0)


def test_improve_tour_is_never_worse_than_nearest_neighbour_even_if_asymmetric():
    rng = random.Random(11)
    size = 15
    matrix = [
        [0.0 if a == b else 0.1 + rng.random() * 10 for b in range(size)] for a in range(size)
    ]
    start = nearest_neighbour(matrix)
    result = improve_tour(matrix)
    assert result[0] == 0
    assert sorted(result) == list(range(size))
    assert tour_length(matrix, result) <= tour_length(matrix, start) + 1e-9


def test_sweep_assign_covers_every_stop_exactly_once_with_balanced_groups():
    for n in (1, 7, 40):
        stops = random_stops(n, seed=n)
        for vehicles in (1, 2, 3, 5, 10):
            groups = sweep_assign(DEPOT, stops, vehicles)
            flat = [i for g in groups for i in g]
            assert sorted(flat) == list(range(n))
            assert len(groups) == min(vehicles, n)
            sizes = [len(g) for g in groups]
            assert max(sizes) - min(sizes) <= 1


def test_sweep_assign_groups_by_angle():
    depot = (0.0, 0.0)
    stops = [(0.1, 0.0), (0.0, 0.1), (-0.1, 0.0), (0.0, -0.1)]  # N, E, S, W
    assert sweep_assign(depot, stops, 2) == [[1, 0], [3, 2]]
    assert sweep_assign(depot, stops, 4) == [[1], [0], [3], [2]]


def test_sweep_assign_edge_cases():
    assert sweep_assign(DEPOT, [], 3) == []
    with pytest.raises(SolverError):
        sweep_assign(DEPOT, [(6.5, 3.5)], 0)


def test_solve_vrp_covers_every_stop_exactly_once():
    stops, matrix = make_instance(45, seed=8)
    for vehicles in (1, 2, 3, 4):
        solution = solve_vrp(matrix, DEPOT, stops, vehicles)
        flat = [s for route in solution.routes for s in route]
        assert sorted(flat) == list(range(45))
        assert len(solution.routes) == len(solution.route_lengths)
        for route, length in zip(solution.routes, solution.route_lengths, strict=True):
            assert length == pytest.approx(route_length(matrix, route))
        assert solution.total_distance == pytest.approx(sum(solution.route_lengths))


def test_solve_vrp_beats_the_original_order():
    stops, matrix = make_instance(40, seed=21)
    for vehicles in (1, 3):
        solution = solve_vrp(matrix, DEPOT, stops, vehicles)
        assert solution.total_distance <= baseline_distance(matrix, vehicles)


def test_solve_vrp_with_no_stops():
    matrix = distance_matrix([DEPOT])
    solution = solve_vrp(matrix, DEPOT, [], 2)
    assert solution.routes == []
    assert solution.total_distance == 0.0


def test_baseline_single_vehicle_is_the_given_order():
    _, matrix = make_instance(10, seed=2)
    assert baseline_distance(matrix, 1) == pytest.approx(
        tour_length(matrix, list(range(len(matrix))))
    )


def test_baseline_with_no_stops_is_zero():
    assert baseline_distance(distance_matrix([DEPOT]), 3) == 0.0


def test_solver_rejects_bad_input():
    stops = [(6.5, 3.5)]
    with pytest.raises(SolverError, match="square"):
        solve_vrp([[0.0, 1.0], [1.0]], DEPOT, stops)
    with pytest.raises(SolverError, match="infinite"):
        solve_vrp([[0.0, float("inf")], [float("inf"), 0.0]], DEPOT, stops)
    with pytest.raises(SolverError, match="number of stops"):
        solve_vrp([[0.0, 1.0], [1.0, 0.0]], DEPOT, [(6.5, 3.5), (6.6, 3.6)])
    with pytest.raises(SolverError, match="num_vehicles"):
        baseline_distance([[0.0, 1.0], [1.0, 0.0]], 0)
