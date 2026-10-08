import random
import time

import pytest

from routewise.auth import create_tenant
from routewise.geo import haversine
from routewise.jobs import (
    GraphCache,
    JobInputError,
    JobManager,
    QueueFullError,
    request_fingerprint,
    solve_request,
)
from routewise.schemas import OptimizeRequest, OptimizeResult


def grid_graph(size: int = 5) -> dict:
    nodes = []
    coords = {}
    for r in range(size):
        for c in range(size):
            node_id = r * size + c
            lat, lng = 6.40 + r * 0.01, 3.30 + c * 0.01
            nodes.append({"id": node_id, "lat": lat, "lng": lng})
            coords[node_id] = (lat, lng)
    edges = []
    for r in range(size):
        for c in range(size):
            here = r * size + c
            for dr, dc in ((0, 1), (1, 0)):
                rr, cc = r + dr, c + dc
                if rr < size and cc < size:
                    other = rr * size + cc
                    weight = haversine(*coords[here], *coords[other]) * 1.25
                    edges.append({"from": here, "to": other, "weight": weight})
    return {"nodes": nodes, "edges": edges}


def make_request(n, mode="haversine", graph_id=None, vehicles=1) -> OptimizeRequest:
    rng = random.Random(5)
    stops = [
        {"id": f"s{i}", "lat": 6.40 + rng.random() * 0.04, "lng": 3.30 + rng.random() * 0.04}
        for i in range(n)
    ]
    return OptimizeRequest.model_validate(
        {
            "depot": {"lat": 6.41, "lng": 3.31},
            "stops": stops,
            "num_vehicles": vehicles,
            "distance_mode": mode,
            "graph_id": graph_id,
        }
    )


def new_job(repo, tenant_id, request=None, key=None):
    request = request or make_request(10)
    job, _ = repo.create_job(
        tenant_id, request.model_dump(mode="json"), request_fingerprint(request), key
    )
    return job


def wait_until_finished(repo, tenant_id, job_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        current = repo.get_job(tenant_id, job_id)
        status = current.status
        if status in ("succeeded", "failed"):
            return status
        time.sleep(0.05)
    return status


@pytest.fixture()
def manager(repo, settings):
    mgr = JobManager(repo, settings)
    yield mgr
    mgr.shutdown()


def test_request_fingerprint_is_stable_and_sensitive():
    assert request_fingerprint(make_request(5)) == request_fingerprint(make_request(5))
    assert request_fingerprint(make_request(5)) != request_fingerprint(make_request(6))
    assert request_fingerprint(make_request(5)) != request_fingerprint(make_request(5, vehicles=2))


def test_solve_request_haversine_covers_every_stop(repo):
    tenant, _ = create_tenant(repo, "A")
    result = solve_request(make_request(20, vehicles=3), tenant.id, repo, GraphCache())
    ids = [stop.id for route in result.routes for stop in route.stops]
    assert sorted(ids) == sorted(f"s{i}" for i in range(20))
    assert len(result.routes) == 3
    assert result.num_vehicles_used == 3
    assert result.accelerator in ("python", "cpp")
    assert result.distance_mode == "haversine"
    for route in result.routes:
        assert [s.sequence for s in route.stops] == list(range(1, len(route.stops) + 1))
    assert result.total_distance_km == pytest.approx(
        sum(route.distance_km for route in result.routes), abs=0.01
    )
    assert result.saved_km == pytest.approx(
        result.baseline_distance_km - result.total_distance_km, abs=0.01
    )


def test_solve_request_single_stop_saves_nothing(repo):
    tenant, _ = create_tenant(repo, "A")
    result = solve_request(make_request(1), tenant.id, repo, GraphCache())
    assert len(result.routes) == 1
    assert result.saved_percent == 0.0
    assert result.total_distance_km > 0


def test_solve_request_graph_mode(repo):
    tenant, _ = create_tenant(repo, "A")
    data = grid_graph()
    record = repo.create_graph(tenant.id, "grid", data, 25, len(data["edges"]))
    request = make_request(10, "graph", record.id, vehicles=2)
    result = solve_request(request, tenant.id, repo, GraphCache())
    ids = [stop.id for route in result.routes for stop in route.stops]
    assert sorted(ids) == sorted(f"s{i}" for i in range(10))
    assert result.distance_mode == "graph"
    assert result.total_distance_km > 0


def test_graph_belonging_to_another_tenant_is_not_found(repo):
    owner, _ = create_tenant(repo, "Owner")
    other, _ = create_tenant(repo, "Other")
    data = grid_graph()
    record = repo.create_graph(owner.id, "grid", data, 25, len(data["edges"]))
    request = make_request(5, "graph", record.id)
    with pytest.raises(JobInputError, match="not found"):
        solve_request(request, other.id, repo, GraphCache())


def test_process_runs_a_job_to_success_and_is_safe_to_repeat(repo, manager):
    tenant, _ = create_tenant(repo, "A")
    job = new_job(repo, tenant.id, make_request(12))
    done = manager.process(tenant.id, job.id)
    assert done is not None
    assert done.status == "succeeded"
    assert done.result is not None
    OptimizeResult.model_validate(done.result)
    again = manager.process(tenant.id, job.id)
    assert again is not None
    assert again.status == "succeeded"


def test_process_marks_unreachable_graph_jobs_as_failed(repo, manager):
    tenant, _ = create_tenant(repo, "A")
    data = {
        "nodes": [{"id": 1, "lat": 6.40, "lng": 3.30}, {"id": 2, "lat": 6.44, "lng": 3.34}],
        "edges": [],
    }
    record = repo.create_graph(tenant.id, "split", data, 2, 0)
    request = OptimizeRequest.model_validate(
        {
            "depot": {"lat": 6.40, "lng": 3.30},
            "stops": [{"id": "x", "lat": 6.44, "lng": 3.34}],
            "distance_mode": "graph",
            "graph_id": record.id,
        }
    )
    job = new_job(repo, tenant.id, request)
    done = manager.process(tenant.id, job.id)
    assert done is not None
    assert done.status == "failed"
    assert "cannot reach" in done.error


def test_unexpected_errors_are_hidden_from_the_client(repo, settings):
    def exploding_optimizer(matrix, tour, max_passes=100):
        raise RuntimeError("secret internal detail")

    mgr = JobManager(repo, settings, optimizer=exploding_optimizer)
    try:
        tenant, _ = create_tenant(repo, "A")
        job = new_job(repo, tenant.id, make_request(8))
        done = mgr.process(tenant.id, job.id)
    finally:
        mgr.shutdown()
    assert done is not None
    assert done.status == "failed"
    assert "internal" in done.error
    assert "secret" not in done.error


def test_submit_processes_in_the_background(repo, manager):
    tenant, _ = create_tenant(repo, "A")
    job = new_job(repo, tenant.id, make_request(15))
    manager.submit(tenant.id, job.id)
    assert wait_until_finished(repo, tenant.id, job.id) == "succeeded"
    manager.shutdown()
    assert manager.pending == 0


def test_submit_when_the_queue_is_full_fails_the_job(repo, manager, monkeypatch):
    monkeypatch.setattr("routewise.jobs.MAX_PENDING_JOBS", 0)
    tenant, _ = create_tenant(repo, "A")
    job = new_job(repo, tenant.id)
    with pytest.raises(QueueFullError):
        manager.submit(tenant.id, job.id)
    stored = repo.get_job(tenant.id, job.id)
    assert stored is not None
    assert stored.status == "failed"
    assert "busy" in stored.error


def test_recover_requeues_and_runs_unfinished_jobs(repo, manager):
    tenant, _ = create_tenant(repo, "A")
    waiting = new_job(repo, tenant.id)
    crashed = new_job(repo, tenant.id)
    repo.mark_running(tenant.id, crashed.id)  # simulates a process that died mid-job

    assert manager.recover() == 2
    assert wait_until_finished(repo, tenant.id, waiting.id) == "succeeded"
    assert wait_until_finished(repo, tenant.id, crashed.id) == "succeeded"
