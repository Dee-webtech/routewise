"""For generating a synthetic city road graph (JSON) for tests and benchmarks."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

from routewise.geo import haversine


def make_city_graph(
    size: int = 30,
    seed: int = 1,
    origin: tuple[float, float] = (6.40, 3.30),
    step: float = 0.003,
    drop: float = 0.1,
) -> dict[str, Any]:
    """Jittered size x size grid. Rows are fully linked and column 0 joins the rows,
    so the graph is always connected; other vertical edges drop with probability `drop`.
    """
    if size < 2:
        raise ValueError("size must be at least 2")
    rng = random.Random(seed)
    jitter = step * 0.2
    coords: dict[int, tuple[float, float]] = {}
    for r in range(size):
        for c in range(size):
            coords[r * size + c] = (
                round(origin[0] + r * step + rng.uniform(-jitter, jitter), 6),
                round(origin[1] + c * step + rng.uniform(-jitter, jitter), 6),
            )

    edges: list[dict[str, Any]] = []

    def connect(a: int, b: int, low: float, span: float) -> None:
        weight = haversine(*coords[a], *coords[b]) * (low + rng.random() * span)
        edges.append({"from": a, "to": b, "weight": math.ceil(weight * 1e6) / 1e6})

    for r in range(size):
        for c in range(size):
            here = r * size + c
            if c + 1 < size:
                connect(here, here + 1, 1.05, 0.6)
            if r + 1 < size and (c == 0 or rng.random() >= drop):
                connect(here, here + size, 1.05, 0.6)
            if r + 1 < size and c + 1 < size and rng.random() < 0.03:
                connect(here, here + size + 1, 1.02, 0.2)

    nodes = [{"id": i, "lat": lat, "lng": lng} for i, (lat, lng) in coords.items()]
    return {"name": f"synthetic-city-{size}x{size}", "nodes": nodes, "edges": edges}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a synthetic city graph")
    parser.add_argument("--size", type=int, default=30, help="grid side length (nodes = size^2)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", default="city_graph.json")
    args = parser.parse_args(argv)
    try:
        data = make_city_graph(args.size, args.seed)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    Path(args.out).write_text(json.dumps(data), encoding="utf-8")
    print(f"Wrote {args.out}: {len(data['nodes'])} nodes, {len(data['edges'])} edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
