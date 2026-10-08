"""Road graph with hand-written Dijkstra and A*.

Edge weights (km) must be >= the straight-line distance between their endpoints.
That keeps the haversine heuristic admissible, so A* stays optimal.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .geo import haversine

INF = math.inf
_ADMISSIBLE_TOLERANCE = 1e-6


class GraphError(ValueError):
    """Raised when a graph document is invalid."""


@dataclass
class PathResult:
    cost: float
    path: list[int]
    expanded: int


class Graph:
    def __init__(self) -> None:
        self.coords: dict[int, tuple[float, float]] = {}
        self.adj: dict[int, list[tuple[int, float]]] = {}

    def add_node(self, node_id: int, lat: float, lng: float) -> None:
        if node_id in self.coords:
            raise GraphError(f"duplicate node id {node_id}")
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
            raise GraphError(f"node {node_id} has out-of-range coordinates")
        self.coords[node_id] = (float(lat), float(lng))
        self.adj[node_id] = []

    def add_edge(self, a: int, b: int, weight: float, oneway: bool = False) -> None:
        if a not in self.coords or b not in self.coords:
            raise GraphError(f"edge {a}->{b} references an unknown node")
        if not math.isfinite(weight) or weight <= 0:
            raise GraphError(f"edge {a}->{b} must have a positive finite weight")
        straight = haversine(*self.coords[a], *self.coords[b])
        if weight < straight * (1.0 - _ADMISSIBLE_TOLERANCE):
            raise GraphError(
                f"edge {a}->{b} weight {weight:.6f} km is shorter than the straight-line "
                f"distance {straight:.6f} km (weights must be >= straight-line distance)"
            )
        self.adj[a].append((b, float(weight)))
        if not oneway:
            self.adj[b].append((a, float(weight)))

    @classmethod
    def from_dict(cls, data: dict[str, Any], max_nodes: int | None = None) -> Graph:
        """Build and validate a graph from {"nodes": [...], "edges": [...]}."""
        if not isinstance(data, dict):
            raise GraphError("graph must be a JSON object")
        nodes = data.get("nodes")
        edges = data.get("edges")
        if not isinstance(nodes, list) or not nodes:
            raise GraphError("graph needs a non-empty 'nodes' list")
        if not isinstance(edges, list):
            raise GraphError("graph needs an 'edges' list")
        if max_nodes is not None and len(nodes) > max_nodes:
            raise GraphError(f"graph has {len(nodes)} nodes; the limit is {max_nodes}")
        graph = cls()
        for node in nodes:
            try:
                graph.add_node(int(node["id"]), float(node["lat"]), float(node["lng"]))
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, GraphError):
                    raise
                raise GraphError(f"invalid node entry: {node!r}") from exc
        for edge in edges:
            try:
                a = int(edge["from"])
                b = int(edge["to"])
                w = float(edge["weight"])
                oneway = bool(edge.get("oneway", False))
            except (KeyError, TypeError, ValueError) as exc:
                raise GraphError(f"invalid edge entry: {edge!r}") from exc
            graph.add_edge(a, b, w, oneway)
        return graph

    @property
    def node_count(self) -> int:
        return len(self.coords)

    def nearest_node(self, lat: float, lng: float) -> int:
        """Id of the closest node. O(V)."""
        best_id = -1
        best = INF
        for node_id, (nlat, nlng) in self.coords.items():
            d = haversine(lat, lng, nlat, nlng)
            if d < best:
                best = d
                best_id = node_id
        if best_id == -1:
            raise GraphError("graph has no nodes")
        return best_id

    def dijkstra_search(
        self, source: int, target: int | None = None
    ) -> tuple[dict[int, float], dict[int, int], int]:
        """Binary-heap Dijkstra with lazy deletion. O((V + E) log V).

        Returns (dist, prev, expanded); stops early once target is settled.
        """
        if source not in self.coords:
            raise GraphError(f"unknown source node {source}")
        dist: dict[int, float] = {source: 0.0}
        prev: dict[int, int] = {}
        done: set[int] = set()
        heap: list[tuple[float, int]] = [(0.0, source)]
        expanded = 0
        while heap:
            d, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            expanded += 1
            if u == target:
                break
            for v, w in self.adj[u]:
                nd = d + w
                if nd < dist.get(v, INF):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
        return dist, prev, expanded

    def dijkstra(self, source: int, target: int) -> PathResult | None:
        """Shortest path, or None if unreachable. O((V + E) log V)."""
        if target not in self.coords:
            raise GraphError(f"unknown target node {target}")
        dist, prev, expanded = self.dijkstra_search(source, target)
        if target not in dist:
            return None
        return PathResult(dist[target], _rebuild(prev, source, target), expanded)

    def astar(self, source: int, target: int) -> PathResult | None:
        """A* with an admissible haversine heuristic. O((V + E) log V) worst case."""
        if source not in self.coords:
            raise GraphError(f"unknown source node {source}")
        if target not in self.coords:
            raise GraphError(f"unknown target node {target}")
        tlat, tlng = self.coords[target]

        def h(node: int) -> float:
            lat, lng = self.coords[node]
            return haversine(lat, lng, tlat, tlng)

        g: dict[int, float] = {source: 0.0}
        prev: dict[int, int] = {}
        closed: set[int] = set()
        heap: list[tuple[float, float, int]] = [(h(source), 0.0, source)]
        expanded = 0
        while heap:
            _f, gu, u = heapq.heappop(heap)
            if u in closed:
                continue
            closed.add(u)
            expanded += 1
            if u == target:
                return PathResult(gu, _rebuild(prev, source, target), expanded)
            for v, w in self.adj[u]:
                ng = gu + w
                if ng < g.get(v, INF):
                    g[v] = ng
                    prev[v] = u
                    heapq.heappush(heap, (ng + h(v), ng, v))
        return None

    def shortest_path(self, source: int, target: int, algo: str = "dijkstra") -> PathResult | None:
        if algo == "dijkstra":
            return self.dijkstra(source, target)
        if algo == "astar":
            return self.astar(source, target)
        raise GraphError(f"unknown algorithm {algo!r}; use 'dijkstra' or 'astar'")

    def cost_matrix(self, node_ids: Sequence[int]) -> list[list[float]]:
        """Shortest-path costs between nodes (inf if unreachable).

        One full Dijkstra per distinct node: O(k (V + E) log V).
        """
        cache: dict[int, dict[int, float]] = {}
        for node in node_ids:
            if node not in cache:
                cache[node] = self.dijkstra_search(node)[0]
        return [[cache[a].get(b, INF) for b in node_ids] for a in node_ids]


def _rebuild(prev: dict[int, int], source: int, target: int) -> list[int]:
    path = [target]
    while path[-1] != source:
        path.append(prev[path[-1]])
    path.reverse()
    return path
