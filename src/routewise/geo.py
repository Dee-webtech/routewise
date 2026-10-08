"""Geographic helpers. All distances are in kilometres."""

from __future__ import annotations

import math
from collections.abc import Sequence

EARTH_RADIUS_KM = 6371.0088

Point = tuple[float, float]  # (lat, lng) in degrees


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points in km.

    Time complexity: O(1).
    """
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def distance_matrix(points: Sequence[Point]) -> list[list[float]]:
    """Symmetric haversine distance matrix for ``points``.

    Time complexity: O(n^2) haversine evaluations (each pair computed once).
    Space complexity: O(n^2).
    """
    n = len(points)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        lat_i, lng_i = points[i]
        for j in range(i + 1, n):
            d = haversine(lat_i, lng_i, points[j][0], points[j][1])
            matrix[i][j] = d
            matrix[j][i] = d
    return matrix


def sweep_angle(depot: Point, point: Point) -> float:
    """Polar angle of ``point`` around ``depot`` in radians, in [0, 2*pi).

    Uses a local equirectangular projection (longitude scaled by cos(latitude)),
    which is accurate for city-scale areas. Does not handle the antimeridian.

    Time complexity: O(1).
    """
    dy = point[0] - depot[0]
    dx = (point[1] - depot[1]) * math.cos(math.radians(depot[0]))
    return math.atan2(dy, dx) % (2.0 * math.pi)
