"""Convert an OpenStreetMap Overpass JSON export into a RouteWise graph document."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import deque
from pathlib import Path
from typing import Any

from routewise.geo import haversine

ONEWAY_VALUES = {"yes", "true", "1", "-1"}


def _largest_component(edges: list[dict[str, Any]]) -> set[int]:
    adjacency: dict[int, set[int]] = {}
    for edge in edges:
        adjacency.setdefault(edge["from"], set()).add(edge["to"])
        adjacency.setdefault(edge["to"], set()).add(edge["from"])
    seen: set[int] = set()
    best: set[int] = set()
    for start in adjacency:
        if start in seen:
            continue
        component = {start}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            for other in adjacency[node]:
                if other not in component:
                    component.add(other)
                    queue.append(other)
        seen |= component
        if len(component) > len(best):
            best = component
    return best


def convert(osm: dict[str, Any]) -> dict[str, Any]:
    """Build {"nodes", "edges"} from Overpass elements. Weights are segment lengths in km."""
    elements = osm.get("elements", [])
    coords: dict[int, tuple[float, float]] = {}
    for element in elements:
        if element.get("type") == "node" and "lat" in element and "lon" in element:
            coords[int(element["id"])] = (float(element["lat"]), float(element["lon"]))

    edges: list[dict[str, Any]] = []
    for element in elements:
        tags = element.get("tags") or {}
        if element.get("type") != "way" or "highway" not in tags:
            continue
        refs = [int(ref) for ref in element.get("nodes", []) if int(ref) in coords]
        oneway = str(tags.get("oneway", "no")).lower()
        for a, b in zip(refs, refs[1:], strict=False):
            if a == b:
                continue
            weight = math.ceil(haversine(*coords[a], *coords[b]) * 1e6) / 1e6
            if weight <= 0:
                continue
            if oneway == "-1":
                a, b = b, a
            edges.append({"from": a, "to": b, "weight": weight, "oneway": oneway in ONEWAY_VALUES})

    keep = _largest_component(edges)
    edges = [e for e in edges if e["from"] in keep and e["to"] in keep]
    nodes = [{"id": n, "lat": coords[n][0], "lng": coords[n][1]} for n in sorted(keep)]
    return {"nodes": nodes, "edges": edges}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Overpass JSON to RouteWise graph")
    parser.add_argument("input", help="Overpass JSON file")
    parser.add_argument("--out", default="graph.json")
    parser.add_argument("--name", default=None, help="optional graph name")
    args = parser.parse_args(argv)
    try:
        osm = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {args.input}: {exc}", file=sys.stderr)
        return 2
    graph = convert(osm)
    if not graph["nodes"]:
        print("error: no roads (ways tagged 'highway') found in the input", file=sys.stderr)
        return 1
    if args.name:
        graph = {"name": args.name, **graph}
    Path(args.out).write_text(json.dumps(graph), encoding="utf-8")
    print(f"Wrote {args.out}: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
