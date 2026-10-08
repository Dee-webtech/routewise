"""Uses the C++ 2-opt when the optional extension is built, pure Python otherwise."""

from __future__ import annotations

from . import solver

try:
    from . import _cpp_two_opt

    HAS_CPP = True
except ImportError:  # extension not built: this is a normal, supported setup
    _cpp_two_opt = None
    HAS_CPP = False

python_two_opt = solver.two_opt
two_opt_cpp = _cpp_two_opt.two_opt if HAS_CPP else None


def two_opt(matrix: list[list[float]], tour: list[int], max_passes: int = 100) -> list[int]:
    """2-opt local search using C++ when available, pure Python otherwise."""
    if two_opt_cpp is not None:
        return list(two_opt_cpp(matrix, tour, max_passes))
    return python_two_opt(matrix, tour, max_passes)
