"""Route construction and improvement heuristics.

A matrix is a square list of distances in km; index 0 is the depot. A tour starts
at 0 and the return leg is implicit. 2-opt and Or-opt assume a symmetric matrix,
so improve_tour never returns anything longer than its nearest-neighbour start.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .geo import Point, sweep_angle

Matrix = list[list[float]]
TwoOptFn = Callable[[Matrix, list[int], int], list[int]]

EPS = 1e-12


class SolverError(ValueError):
    """Raised when solver input is invalid."""


def validate_matrix(matrix: Matrix) -> None:
    """Matrix must be square with finite entries. O(n^2)."""
    size = len(matrix)
    for row in matrix:
        if len(row) != size:
            raise SolverError("distance matrix must be square")
        for value in row:
            if not math.isfinite(value):
                raise SolverError(
                    "some stops cannot reach each other (infinite distance); "
                    "check that the road graph connects them"
                )


def tour_length(matrix: Matrix, tour: Sequence[int]) -> float:
    """Closed tour length. O(n)."""
    n = len(tour)
    if n < 2:
        return 0.0
    total = 0.0
    for k in range(n):
        total += matrix[tour[k]][tour[(k + 1) % n]]
    return total


def nearest_neighbour(matrix: Matrix, start: int = 0) -> list[int]:
    """Greedy construction, deterministic on ties. O(n^2)."""
    n = len(matrix)
    if n == 0:
        return []
    tour = [start]
    remaining = [i for i in range(n) if i != start]
    current = start
    while remaining:
        row = matrix[current]
        best = min(remaining, key=row.__getitem__)
        remaining.remove(best)
        tour.append(best)
        current = best
    return tour


def two_opt(matrix: Matrix, tour: list[int], max_passes: int = 100) -> list[int]:
    """2-opt with first improvement; position 0 stays fixed. O(n^2) per pass.

    Returns a new list; the input is not modified.
    """
    tour = list(tour)
    n = len(tour)
    if n < 4:
        return tour
    improved = True
    passes = 0
    while improved and passes < max_passes:
        improved = False
        passes += 1
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                a = tour[i - 1]
                b = tour[i]
                c = tour[j]
                d = tour[(j + 1) % n]
                delta = (matrix[a][c] + matrix[b][d]) - (matrix[a][b] + matrix[c][d])
                if delta < -EPS:
                    tour[i : j + 1] = tour[i : j + 1][::-1]
                    improved = True
    return tour


def or_opt(
    matrix: Matrix, tour: list[int], max_segment: int = 3, max_passes: int = 50
) -> list[int]:
    """Or-opt: relocate segments of 1..max_segment stops, in either orientation.

    O(n^2) per pass. Total applied moves are capped at 50 * n so it always ends.
    """
    tour = list(tour)
    n = len(tour)
    if n < 5:
        return tour
    max_moves = 50 * n
    moves = 0
    longest = min(max_segment, n - 3)
    for _ in range(max_passes):
        improved = False
        for seg_len in range(1, longest + 1):
            i = 1
            while i + seg_len <= n:
                prev = tour[i - 1]
                seg = tour[i : i + seg_len]
                nxt = tour[(i + seg_len) % n]
                removal_gain = matrix[prev][seg[0]] + matrix[seg[-1]][nxt] - matrix[prev][nxt]
                rest = tour[:i] + tour[i + seg_len :]
                size = len(rest)
                best_delta = -EPS
                best_k = -1
                best_reversed = False
                for k in range(size):
                    p = rest[k]
                    q = rest[(k + 1) % size]
                    base = matrix[p][q]
                    if k != i - 1:  # i - 1 is the segment's current gap
                        forward = matrix[p][seg[0]] + matrix[seg[-1]][q] - base - removal_gain
                        if forward < best_delta:
                            best_delta = forward
                            best_k = k
                            best_reversed = False
                    backward = matrix[p][seg[-1]] + matrix[seg[0]][q] - base - removal_gain
                    if backward < best_delta:
                        best_delta = backward
                        best_k = k
                        best_reversed = True
                if best_k >= 0:
                    inserted = seg[::-1] if best_reversed else seg
                    tour = rest[: best_k + 1] + inserted + rest[best_k + 1 :]
                    improved = True
                    moves += 1
                    if moves >= max_moves:
                        return tour
                    continue
                i += 1
        if not improved:
            break
    return tour


def improve_tour(matrix: Matrix, optimizer: TwoOptFn = two_opt) -> list[int]:
    """Nearest-neighbour, 2-opt, Or-opt, 2-opt; never worse than the start. O(n^2) per pass."""
    start = nearest_neighbour(matrix)
    tour = optimizer(matrix, start, 100)
    tour = or_opt(matrix, tour)
    tour = optimizer(matrix, tour, 100)
    if tour_length(matrix, tour) <= tour_length(matrix, start):
        return tour
    return start


def sweep_assign(depot: Point, stops: Sequence[Point], num_vehicles: int) -> list[list[int]]:
    """Sort stops by angle around the depot and cut into near-equal groups. O(n log n).

    Returns groups of 0-based stop indices; empty groups are omitted.
    """
    if num_vehicles < 1:
        raise SolverError("num_vehicles must be at least 1")
    n = len(stops)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: (sweep_angle(depot, stops[i]), i))
    vehicles = min(num_vehicles, n)
    base, extra = divmod(n, vehicles)
    groups: list[list[int]] = []
    pos = 0
    for k in range(vehicles):
        size = base + (1 if k < extra else 0)
        groups.append(order[pos : pos + size])
        pos += size
    return groups


@dataclass
class Solution:
    routes: list[list[int]]  # 0-based stop indices per vehicle, in visiting order
    route_lengths: list[float]
    total_distance: float


def solve_vrp(
    matrix: Matrix,
    depot: Point,
    stops: Sequence[Point],
    num_vehicles: int = 1,
    optimizer: TwoOptFn = two_opt,
) -> Solution:
    """Sweep, then optimise each vehicle's route. O(n log n) plus O(m^2) per pass per vehicle."""
    validate_matrix(matrix)
    if len(matrix) != len(stops) + 1:
        raise SolverError("distance matrix size must equal the number of stops plus one")
    groups = sweep_assign(depot, stops, num_vehicles)
    routes: list[list[int]] = []
    lengths: list[float] = []
    for group in groups:
        idx = [0] + [g + 1 for g in group]
        sub = [[matrix[a][b] for b in idx] for a in idx]
        tour = improve_tour(sub, optimizer)
        routes.append([idx[t] - 1 for t in tour[1:]])
        lengths.append(tour_length(sub, tour))
    return Solution(routes=routes, route_lengths=lengths, total_distance=math.fsum(lengths))


def baseline_distance(matrix: Matrix, num_vehicles: int = 1) -> float:
    """Distance of the original order, split into contiguous chunks per vehicle. O(n)."""
    n = len(matrix) - 1
    if n <= 0:
        return 0.0
    if num_vehicles < 1:
        raise SolverError("num_vehicles must be at least 1")
    vehicles = min(num_vehicles, n)
    base, extra = divmod(n, vehicles)
    total = 0.0
    pos = 1
    for k in range(vehicles):
        size = base + (1 if k < extra else 0)
        chunk = [0] + list(range(pos, pos + size))
        total += tour_length(matrix, chunk)
        pos += size
    return total
