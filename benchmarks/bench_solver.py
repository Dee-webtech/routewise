"""Benchmarks: Python vs C++ 2-opt, and Dijkstra vs A*. Prints a markdown report."""

from __future__ import annotations

import argparse
import platform
import random
import sys
import time
from pathlib import Path

from routewise import _fast, solver
from routewise.geo import distance_matrix
from routewise.graph import Graph

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
QUICK = {"sizes": (50, 100), "repeats": 1, "graph_size": 20, "queries": 50}


def best_time(fn, repeats, *args):
    """Fastest of `repeats` calls in milliseconds, plus the last result."""
    best = float("inf")
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = fn(*args)
        best = min(best, time.perf_counter() - started)
    return best * 1000.0, result


def random_matrix(stops, seed):
    rng = random.Random(seed)
    points = [(6.40 + rng.random() * 0.2, 3.30 + rng.random() * 0.2) for _ in range(stops + 1)]
    return distance_matrix(points)


def two_opt_table(sizes, repeats):
    lines = [
        "| Stops | Python (ms) | C++ (ms) | Speedup | Identical tours |",
        "|---:|---:|---:|---:|:---:|",
    ]
    for stops in sizes:
        matrix = random_matrix(stops, seed=stops)
        start = solver.nearest_neighbour(matrix)
        py_ms, py_tour = best_time(solver.two_opt, repeats, matrix, start, 100)
        if _fast.HAS_CPP:
            cpp_ms, cpp_tour = best_time(_fast.two_opt_cpp, repeats, matrix, start, 100)
            same = "yes" if list(cpp_tour) == py_tour else "NO"
            speedup = py_ms / max(cpp_ms, 1e-9)
            lines.append(f"| {stops} | {py_ms:.1f} | {cpp_ms:.1f} | {speedup:.1f}x | {same} |")
        else:
            lines.append(f"| {stops} | {py_ms:.1f} | not built | n/a | n/a |")
    return "\n".join(lines)


def search_table(graph_size, queries):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from make_city_graph import make_city_graph

    graph = Graph.from_dict(make_city_graph(size=graph_size, seed=3))
    rng = random.Random(3)
    nodes = list(graph.coords)
    pairs = [(rng.choice(nodes), rng.choice(nodes)) for _ in range(queries)]
    expanded = {"dijkstra": 0, "astar": 0}
    elapsed = {"dijkstra": 0.0, "astar": 0.0}
    mismatches = 0
    for source, target in pairs:
        costs = {}
        for algo in expanded:
            started = time.perf_counter()
            found = graph.shortest_path(source, target, algo)
            elapsed[algo] += time.perf_counter() - started
            if found is None:
                raise RuntimeError("the synthetic city graph should be connected")
            expanded[algo] += found.expanded
            costs[algo] = found.cost
        if abs(costs["dijkstra"] - costs["astar"]) > 1e-9 * max(1.0, costs["dijkstra"]):
            mismatches += 1
    d_avg = expanded["dijkstra"] / queries
    a_avg = expanded["astar"] / queries
    ratio = a_avg / d_avg * 100.0 if d_avg else 0.0
    d_ms = elapsed["dijkstra"] * 1000.0 / queries
    a_ms = elapsed["astar"] * 1000.0 / queries
    equal = "yes" if mismatches == 0 else f"NO ({mismatches} differ)"
    return "\n".join(
        [
            "| Nodes | Queries | Dijkstra expanded (avg) | A* expanded (avg) | A* / Dijkstra"
            " | Dijkstra (ms/query) | A* (ms/query) | Equal costs |",
            "|---:|---:|---:|---:|---:|---:|---:|:---:|",
            f"| {graph.node_count} | {queries} | {d_avg:.1f} | {a_avg:.1f} | {ratio:.1f}% "
            f"| {d_ms:.3f} | {a_ms:.3f} | {equal} |",
        ]
    )


def build_report(sizes=(100, 200, 400), repeats=3, graph_size=30, queries=100):
    cpp = "yes" if _fast.HAS_CPP else "no"
    return "\n".join(
        [
            f"Python {platform.python_version()} on {platform.system()} {platform.machine()}. "
            f"C++ extension built: {cpp}.",
            "",
            f"### 2-opt local search (nearest-neighbour start, best of {repeats} runs)",
            "",
            two_opt_table(sizes, repeats),
            "",
            "The C++ timings include converting the Python distance matrix on every call.",
            "",
            f"### Dijkstra vs A* ({graph_size}x{graph_size} synthetic city grid)",
            "",
            search_table(graph_size, queries),
            "",
            "Timings depend on the machine. Node counts do not.",
        ]
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RouteWise benchmarks")
    parser.add_argument("--quick", action="store_true", help="small sizes, one repeat")
    parser.add_argument("--out", help="also write the report to this file")
    args = parser.parse_args(argv)
    report = build_report(**QUICK) if args.quick else build_report()
    print(report)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
