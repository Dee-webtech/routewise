import pytest

from bench_solver import build_report
from make_city_graph import make_city_graph
from osm_to_graph import convert
from routewise.graph import Graph


def test_city_graph_is_valid_connected_and_deterministic():
    data = make_city_graph(size=10, seed=1)
    graph = Graph.from_dict(data)
    assert graph.node_count == 100
    dist, _, _ = graph.dijkstra_search(0)
    assert len(dist) == graph.node_count
    assert make_city_graph(size=10, seed=1) == data
    assert make_city_graph(size=10, seed=2) != data


def test_city_graph_astar_matches_dijkstra():
    graph = Graph.from_dict(make_city_graph(size=12, seed=4))
    for source, target in ((0, 143), (5, 100), (30, 31)):
        d = graph.dijkstra(source, target)
        a = graph.astar(source, target)
        assert d is not None and a is not None
        assert a.cost == pytest.approx(d.cost, rel=1e-9)


def test_city_graph_rejects_tiny_sizes():
    with pytest.raises(ValueError):
        make_city_graph(size=1)


def osm_fixture():
    return {
        "elements": [
            {"type": "node", "id": 1, "lat": 6.400, "lon": 3.300},
            {"type": "node", "id": 2, "lat": 6.401, "lon": 3.300},
            {"type": "node", "id": 3, "lat": 6.402, "lon": 3.300},
            {"type": "node", "id": 4, "lat": 6.402, "lon": 3.301},
            {"type": "node", "id": 5, "lat": 6.500, "lon": 3.400},
            {"type": "node", "id": 6, "lat": 6.501, "lon": 3.400},
            {"type": "way", "id": 10, "nodes": [1, 2, 3], "tags": {"highway": "residential"}},
            {
                "type": "way",
                "id": 11,
                "nodes": [3, 4],
                "tags": {"highway": "primary", "oneway": "yes"},
            },
            {"type": "way", "id": 12, "nodes": [5, 6], "tags": {"highway": "service"}},
            {"type": "way", "id": 13, "nodes": [1, 4], "tags": {"building": "yes"}},
        ]
    }


def test_osm_conversion_keeps_the_largest_component_and_oneway_flags():
    data = convert(osm_fixture())
    assert [n["id"] for n in data["nodes"]] == [1, 2, 3, 4]
    assert len(data["edges"]) == 3
    oneway = [e for e in data["edges"] if e["oneway"]]
    assert [(e["from"], e["to"]) for e in oneway] == [(3, 4)]

    graph = Graph.from_dict(data)
    assert graph.dijkstra(1, 4) is not None
    assert graph.dijkstra(4, 1) is None


def test_osm_conversion_without_roads_is_empty():
    data = convert({"elements": [{"type": "node", "id": 1, "lat": 0.0, "lon": 0.0}]})
    assert data == {"nodes": [], "edges": []}


def test_benchmark_report_smoke():
    report = build_report(sizes=(30,), repeats=1, graph_size=10, queries=10)
    assert "| 30 |" in report
    assert "Dijkstra" in report
    assert "A*" in report
