import random

import pytest

from routewise.geo import haversine
from routewise.graph import Graph, GraphError


def make_grid_graph(seed: int, size: int = 12) -> Graph:
    """Connected grid with irregular weights, all >= straight-line distance."""
    rng = random.Random(seed)
    graph = Graph()
    for r in range(size):
        for c in range(size):
            graph.add_node(r * size + c, 6.40 + r * 0.01, 3.30 + c * 0.01)
    for r in range(size):
        for c in range(size):
            here = r * size + c
            for dr, dc in ((0, 1), (1, 0)):
                rr, cc = r + dr, c + dc
                if rr < size and cc < size:
                    other = rr * size + cc
                    straight = haversine(*graph.coords[here], *graph.coords[other])
                    graph.add_edge(here, other, straight * (1.0 + rng.random() * 0.6))
    for _ in range(size):
        a = rng.randrange(size * size)
        b = rng.randrange(size * size)
        if a != b:
            straight = haversine(*graph.coords[a], *graph.coords[b])
            graph.add_edge(a, b, straight * (1.0 + rng.random() * 0.3))
    return graph


def path_cost(graph: Graph, path: list[int]) -> float:
    total = 0.0
    for k in range(len(path) - 1):
        a, b = path[k], path[k + 1]
        total += min(w for v, w in graph.adj[a] if v == b)
    return total


def test_dijkstra_and_astar_agree_on_200_random_queries():
    graph = make_grid_graph(seed=7)
    rng = random.Random(99)
    nodes = list(graph.coords)
    dijkstra_expanded = 0
    astar_expanded = 0
    for _ in range(200):
        source = rng.choice(nodes)
        target = rng.choice(nodes)
        d = graph.dijkstra(source, target)
        a = graph.astar(source, target)
        assert d is not None
        assert a is not None
        assert a.cost == pytest.approx(d.cost, rel=1e-9)
        assert d.path[0] == source and d.path[-1] == target
        assert a.path[0] == source and a.path[-1] == target
        assert path_cost(graph, d.path) == pytest.approx(d.cost, rel=1e-9)
        assert path_cost(graph, a.path) == pytest.approx(a.cost, rel=1e-9)
        dijkstra_expanded += d.expanded
        astar_expanded += a.expanded
    assert astar_expanded < dijkstra_expanded


def test_source_equals_target_is_zero_cost():
    graph = make_grid_graph(seed=1)
    for algo in ("dijkstra", "astar"):
        result = graph.shortest_path(5, 5, algo)
        assert result is not None
        assert result.cost == 0.0
        assert result.path == [5]


def test_unreachable_returns_none():
    graph = Graph()
    graph.add_node(1, 0.0, 0.0)
    graph.add_node(2, 0.0, 0.01)
    assert graph.dijkstra(1, 2) is None
    assert graph.astar(1, 2) is None


def test_oneway_edge_is_respected():
    graph = Graph()
    graph.add_node(1, 0.0, 0.0)
    graph.add_node(2, 0.0, 0.01)
    graph.add_edge(1, 2, 1.2, oneway=True)
    forward = graph.dijkstra(1, 2)
    assert forward is not None
    assert forward.cost == pytest.approx(1.2)
    assert graph.dijkstra(2, 1) is None
    assert graph.astar(2, 1) is None


def test_edge_shorter_than_straight_line_is_rejected():
    graph = Graph()
    graph.add_node(1, 0.0, 0.0)
    graph.add_node(2, 0.0, 0.01)
    with pytest.raises(GraphError, match="straight-line"):
        graph.add_edge(1, 2, 0.5)


def test_duplicate_node_unknown_node_and_bad_weight_are_rejected():
    graph = Graph()
    graph.add_node(1, 0.0, 0.0)
    graph.add_node(2, 0.0, 0.01)
    with pytest.raises(GraphError, match="duplicate"):
        graph.add_node(1, 1.0, 1.0)
    with pytest.raises(GraphError, match="unknown node"):
        graph.add_edge(1, 99, 5.0)
    with pytest.raises(GraphError, match="positive"):
        graph.add_edge(1, 2, -1.0)
    with pytest.raises(GraphError, match="out-of-range"):
        graph.add_node(3, 95.0, 0.0)


def test_from_dict_builds_a_valid_graph():
    data = {
        "nodes": [
            {"id": 1, "lat": 0.0, "lng": 0.0},
            {"id": 2, "lat": 0.0, "lng": 0.01},
            {"id": 3, "lat": 0.0, "lng": 0.02},
        ],
        "edges": [
            {"from": 1, "to": 2, "weight": 1.2},
            {"from": 2, "to": 3, "weight": 1.2},
        ],
    }
    graph = Graph.from_dict(data)
    assert graph.node_count == 3
    result = graph.shortest_path(1, 3, "astar")
    assert result is not None
    assert result.cost == pytest.approx(2.4)
    assert result.path == [1, 2, 3]


def test_from_dict_rejects_bad_documents():
    with pytest.raises(GraphError, match="nodes"):
        Graph.from_dict({"edges": []})
    with pytest.raises(GraphError, match="edges"):
        Graph.from_dict({"nodes": [{"id": 1, "lat": 0, "lng": 0}]})
    with pytest.raises(GraphError, match="invalid node"):
        Graph.from_dict({"nodes": [{"id": 1}], "edges": []})
    with pytest.raises(GraphError, match="invalid edge"):
        Graph.from_dict({"nodes": [{"id": 1, "lat": 0, "lng": 0}], "edges": [{"from": 1}]})
    with pytest.raises(GraphError, match="limit"):
        Graph.from_dict(
            {"nodes": [{"id": 1, "lat": 0, "lng": 0}, {"id": 2, "lat": 0, "lng": 1}], "edges": []},
            max_nodes=1,
        )


def test_nearest_node_snaps_to_closest():
    graph = make_grid_graph(seed=3)
    assert graph.nearest_node(6.4001, 3.3001) == 0
    assert graph.nearest_node(6.51, 3.41) == 11 * 12 + 11


def test_cost_matrix_matches_dijkstra_and_marks_unreachable():
    graph = make_grid_graph(seed=5)
    graph.add_node(9999, 7.0, 4.0)  # isolated
    ids = [0, 5, 20, 143, 9999]
    matrix = graph.cost_matrix(ids)
    for i, a in enumerate(ids):
        assert matrix[i][i] == 0.0
        for j, b in enumerate(ids):
            if 9999 in (a, b) and a != b:
                assert matrix[i][j] == float("inf")
            elif a != b:
                expected = graph.dijkstra(a, b)
                assert expected is not None
                assert matrix[i][j] == pytest.approx(expected.cost)
                assert matrix[i][j] == pytest.approx(matrix[j][i])


def test_unknown_algorithm_is_rejected():
    graph = make_grid_graph(seed=2)
    with pytest.raises(GraphError, match="algorithm"):
        graph.shortest_path(0, 1, "bellman-ford")
