import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from routewise.api import create_app
from routewise.geo import haversine

OPTIMIZE = "/api/v1/optimize"
GRAPHS = "/api/v1/graphs"
DEPOT = {"lat": 6.41, "lng": 3.31}


def payload(n=5, **overrides):
    stops = [
        {"id": f"s{i}", "lat": 6.40 + (i * 7 % 10) * 0.005, "lng": 3.30 + (i * 3 % 10) * 0.005}
        for i in range(n)
    ]
    body = {"depot": DEPOT, "stops": stops}
    body.update(overrides)
    return body


def grid_document(size=10, step=0.005):
    coords = {
        r * size + c: (6.40 + r * step, 3.30 + c * step) for r in range(size) for c in range(size)
    }
    nodes = [{"id": i, "lat": lat, "lng": lng} for i, (lat, lng) in coords.items()]
    edges = []
    for r in range(size):
        for c in range(size):
            here = r * size + c
            for rr, cc in ((r, c + 1), (r + 1, c)):
                if rr < size and cc < size:
                    other = rr * size + cc
                    weight = haversine(*coords[here], *coords[other]) * 1.25
                    edges.append({"from": here, "to": other, "weight": weight})
    return {"name": "grid", "nodes": nodes, "edges": edges}


def wait_for_job(client, job_id, headers, timeout=20.0):
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()
        if job["status"] in ("succeeded", "failed") or time.monotonic() > deadline:
            return job
        time.sleep(0.05)


def test_health_ready_docs_and_front_end(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert client.get("/docs").status_code == 200
    assert OPTIMIZE in client.get("/openapi.json").json()["paths"]
    page = client.get("/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "RouteWise" in page.text
    assert "leaflet" in page.text.lower()


def test_readyz_reports_an_unreachable_database(app, client, monkeypatch):
    def broken():
        raise RuntimeError("down")

    monkeypatch.setattr(app.state.repo, "ping", broken)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_ready"


def test_request_ids_are_generated_echoed_and_sanitised(client):
    assert len(client.get("/healthz").headers["x-request-id"]) == 32
    echoed = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"
    replaced = client.get("/healthz", headers={"X-Request-ID": "bad id!"})
    assert replaced.headers["x-request-id"] != "bad id!"
    assert len(replaced.headers["x-request-id"]) == 32


def test_unknown_routes_use_the_standard_error_shape(client):
    response = client.get("/nope")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"] == response.headers["x-request-id"]


def test_missing_or_wrong_keys_are_rejected(client):
    for headers in (
        {},
        {"X-API-Key": "rw_wrong"},
        {"Authorization": "Bearer rw_wrong"},
        {"Authorization": "Basic abc"},
    ):
        response = client.post(OPTIMIZE, json=payload(3), headers=headers)
        assert response.status_code == 401
        error = response.json()["error"]
        assert error["code"] == "unauthorized"
        assert error["request_id"] == response.headers["x-request-id"]
        assert response.headers["www-authenticate"] == "Bearer"


def test_bearer_authorization_is_accepted(client, tenant_a):
    headers = {"Authorization": f"Bearer {tenant_a['key']}"}
    assert client.get("/api/v1/jobs/unknown", headers=headers).status_code == 404


def test_small_request_is_solved_immediately(client, tenant_a):
    response = client.post(OPTIMIZE, json=payload(8, num_vehicles=2), headers=tenant_a["headers"])
    assert response.status_code == 200
    job = response.json()
    assert job["status"] == "succeeded"
    result = job["result"]
    assert result["num_vehicles_used"] == 2
    ids = [stop["id"] for route in result["routes"] for stop in route["stops"]]
    assert sorted(ids) == sorted(f"s{i}" for i in range(8))
    assert result["total_distance_km"] > 0
    assert "saved_percent" in result


def test_job_can_be_fetched_and_unknown_jobs_are_404(client, tenant_a):
    created = client.post(OPTIMIZE, json=payload(5), headers=tenant_a["headers"]).json()
    fetched = client.get(f"/api/v1/jobs/{created['id']}", headers=tenant_a["headers"])
    assert fetched.status_code == 200
    assert fetched.json() == created
    missing = client.get("/api/v1/jobs/does-not-exist", headers=tenant_a["headers"])
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "job_not_found"


def test_large_requests_run_in_the_background(settings, tenant_a):
    headers = {**tenant_a["headers"], "Idempotency-Key": "big-1"}
    with TestClient(create_app(replace(settings, sync_max_stops=3))) as client:
        first = client.post(OPTIMIZE, json=payload(8), headers=headers)
        assert first.status_code == 202
        job_id = first.json()["id"]
        assert first.headers["location"] == f"/api/v1/jobs/{job_id}"
        replay = client.post(OPTIMIZE, json=payload(8), headers=headers)
        assert replay.json()["id"] == job_id
        job = wait_for_job(client, job_id, tenant_a["headers"])
        assert job["status"] == "succeeded"
        assert job["result"]["routes"]


def test_idempotency_key_replays_the_same_job(client, tenant_a, tenant_b):
    headers = {**tenant_a["headers"], "Idempotency-Key": "order-42"}
    first = client.post(OPTIMIZE, json=payload(5), headers=headers)
    second = client.post(OPTIMIZE, json=payload(5), headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["result"] == second.json()["result"]

    changed = client.post(OPTIMIZE, json=payload(6), headers=headers)
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "idempotency_conflict"

    other_headers = {**tenant_b["headers"], "Idempotency-Key": "order-42"}
    other = client.post(OPTIMIZE, json=payload(5), headers=other_headers)
    assert other.json()["id"] != first.json()["id"]

    plain_1 = client.post(OPTIMIZE, json=payload(5), headers=tenant_a["headers"])
    plain_2 = client.post(OPTIMIZE, json=payload(5), headers=tenant_a["headers"])
    assert plain_1.json()["id"] != plain_2.json()["id"]


def test_overlong_idempotency_key_is_rejected(client, tenant_a):
    headers = {**tenant_a["headers"], "Idempotency-Key": "k" * 200}
    response = client.post(OPTIMIZE, json=payload(3), headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (payload(0), "stops"),
        (payload(2, num_vehicles=0), "num_vehicles"),
        (payload(2, distance_mode="graph"), "graph_id"),
        (payload(2, distance_mode="teleport"), "distance_mode"),
        (payload(2, depot={"lat": 95, "lng": 3.3}), "depot"),
        (payload(2, extra_field=1), "extra_field"),
    ],
)
def test_invalid_bodies_return_a_consistent_422(client, tenant_a, body, fragment):
    response = client.post(OPTIMIZE, json=body, headers=tenant_a["headers"])
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert fragment in error["message"]


def test_duplicate_stop_ids_and_malformed_json_are_422(client, tenant_a):
    body = payload(2)
    body["stops"][1]["id"] = body["stops"][0]["id"]
    duplicate = client.post(OPTIMIZE, json=body, headers=tenant_a["headers"])
    assert duplicate.status_code == 422
    assert "duplicate" in duplicate.json()["error"]["message"]

    headers = {**tenant_a["headers"], "Content-Type": "application/json"}
    broken = client.post(OPTIMIZE, content="{not json", headers=headers)
    assert broken.status_code == 422
    assert broken.json()["error"]["code"] == "validation_error"


def test_configured_limits_are_enforced(settings, tenant_a):
    limited = replace(settings, max_stops=3, max_vehicles=2)
    with TestClient(create_app(limited)) as client:
        too_many_stops = client.post(OPTIMIZE, json=payload(4), headers=tenant_a["headers"])
        assert too_many_stops.status_code == 422
        assert too_many_stops.json()["error"]["code"] == "limit_exceeded"
        assert "stops" in too_many_stops.json()["error"]["message"]
        too_many_vehicles = client.post(
            OPTIMIZE, json=payload(2, num_vehicles=3), headers=tenant_a["headers"]
        )
        assert too_many_vehicles.status_code == 422
        assert "vehicles" in too_many_vehicles.json()["error"]["message"]


def test_graph_upload_and_both_shortest_path_algorithms(client, tenant_a):
    headers = tenant_a["headers"]
    created = client.post(GRAPHS, json=grid_document(), headers=headers)
    assert created.status_code == 201
    graph = created.json()
    assert graph["node_count"] == 100
    meta = client.get(f"/api/v1/graphs/{graph['id']}", headers=headers)
    assert meta.json()["name"] == "grid"

    results = {}
    for algo in ("dijkstra", "astar"):
        response = client.get(
            f"/api/v1/graphs/{graph['id']}/path",
            params={"from": 0, "to": 99, "algo": algo},
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["path"][0] == 0
        assert body["path"][-1] == 99
        results[algo] = body
    assert results["astar"]["cost_km"] == pytest.approx(results["dijkstra"]["cost_km"], abs=1e-3)
    assert results["astar"]["nodes_expanded"] <= results["dijkstra"]["nodes_expanded"]


def test_graph_errors(client, tenant_a):
    headers = tenant_a["headers"]
    too_short = {
        "nodes": [{"id": 1, "lat": 0.0, "lng": 0.0}, {"id": 2, "lat": 0.0, "lng": 0.01}],
        "edges": [{"from": 1, "to": 2, "weight": 0.5}],
    }
    bad = client.post(GRAPHS, json=too_short, headers=headers)
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_graph"
    assert "straight-line" in bad.json()["error"]["message"]

    empty = client.post(GRAPHS, json={"nodes": [], "edges": []}, headers=headers)
    assert empty.status_code == 422

    split = {
        "nodes": [{"id": 1, "lat": 6.40, "lng": 3.30}, {"id": 2, "lat": 6.44, "lng": 3.34}],
        "edges": [],
    }
    graph = client.post(GRAPHS, json=split, headers=headers).json()
    path_url = f"/api/v1/graphs/{graph['id']}/path"
    no_path = client.get(path_url, params={"from": 1, "to": 2}, headers=headers)
    assert no_path.status_code == 404
    assert no_path.json()["error"]["code"] == "no_path"
    unknown_node = client.get(path_url, params={"from": 1, "to": 999}, headers=headers)
    assert unknown_node.status_code == 404
    assert unknown_node.json()["error"]["code"] == "node_not_found"
    bad_algo = client.get(path_url, params={"from": 1, "to": 2, "algo": "bfs"}, headers=headers)
    assert bad_algo.status_code == 422
    missing = client.get("/api/v1/graphs/nope/path", params={"from": 1, "to": 2}, headers=headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "graph_not_found"


def test_graph_mode_optimisation(client, tenant_a):
    headers = tenant_a["headers"]
    graph = client.post(GRAPHS, json=grid_document(), headers=headers).json()
    body = payload(6, distance_mode="graph", graph_id=graph["id"])
    response = client.post(OPTIMIZE, json=body, headers=headers)
    assert response.status_code == 200
    job = response.json()
    assert job["status"] == "succeeded"
    assert job["result"]["distance_mode"] == "graph"


def test_unreachable_stops_produce_a_failed_job(client, tenant_a):
    headers = tenant_a["headers"]
    split = {
        "name": "split",
        "nodes": [{"id": 1, "lat": 6.40, "lng": 3.30}, {"id": 2, "lat": 6.44, "lng": 3.34}],
        "edges": [],
    }
    graph = client.post(GRAPHS, json=split, headers=headers).json()
    body = {
        "depot": {"lat": 6.40, "lng": 3.30},
        "stops": [{"id": "x", "lat": 6.44, "lng": 3.34}],
        "distance_mode": "graph",
        "graph_id": graph["id"],
    }
    response = client.post(OPTIMIZE, json=body, headers=headers)
    assert response.status_code == 200
    job = response.json()
    assert job["status"] == "failed"
    assert "cannot reach" in job["error"]


def test_tenant_b_cannot_read_tenant_a_jobs_or_graphs(client, tenant_a, tenant_b):
    a, b = tenant_a["headers"], tenant_b["headers"]
    job = client.post(OPTIMIZE, json=payload(4), headers=a).json()
    graph = client.post(GRAPHS, json=grid_document(), headers=a).json()

    private_paths = [
        f"/api/v1/jobs/{job['id']}",
        f"/api/v1/graphs/{graph['id']}",
        f"/api/v1/graphs/{graph['id']}/path?from=0&to=5",
    ]
    for path in private_paths:
        assert client.get(path, headers=a).status_code == 200
        assert client.get(path, headers=b).status_code == 404

    graph_request = payload(4, distance_mode="graph", graph_id=graph["id"])
    denied = client.post(OPTIMIZE, json=graph_request, headers=b)
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "graph_not_found"
    allowed = client.post(OPTIMIZE, json=graph_request, headers=a)
    assert allowed.json()["status"] == "succeeded"


def test_rate_limiting_is_per_tenant(settings, tenant_a, tenant_b):
    limited = replace(settings, rate_limit_per_minute=1, rate_limit_burst=2)
    with TestClient(create_app(limited)) as client:
        url = "/api/v1/jobs/x"
        codes = [client.get(url, headers=tenant_a["headers"]).status_code for _ in range(3)]
        assert codes == [404, 404, 429]
        blocked = client.get(url, headers=tenant_a["headers"])
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "rate_limited"
        assert int(blocked.headers["retry-after"]) >= 1
        assert client.get(url, headers=tenant_b["headers"]).status_code == 404
