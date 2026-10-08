"""Solving optimize requests and running them as jobs (inline or on a bounded pool)."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from . import _fast
from .config import Settings
from .db import JobRecord, Repository
from .geo import distance_matrix
from .graph import Graph
from .schemas import OptimizeRequest, OptimizeResult, RouteOut, RouteStop
from .solver import TwoOptFn, baseline_distance, solve_vrp

logger = logging.getLogger("routewise.jobs")

MAX_PENDING_JOBS = 1000


class JobInputError(ValueError):
    """The request refers to something that does not exist, such as a graph."""


class QueueFullError(RuntimeError):
    """Too many jobs are already pending."""


def request_fingerprint(request: OptimizeRequest) -> str:
    """Stable hash of the request body, used to detect Idempotency-Key reuse."""
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class GraphCache:
    """Thread-safe LRU of parsed graphs keyed by (tenant_id, graph_id).

    Graphs are immutable once uploaded, so cached copies never go stale.
    """

    def __init__(self, max_items: int = 8) -> None:
        self._max = max_items
        self._items: OrderedDict[tuple[str, str], Graph] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[str, str]) -> Graph | None:
        with self._lock:
            graph = self._items.get(key)
            if graph is not None:
                self._items.move_to_end(key)
            return graph

    def put(self, key: tuple[str, str], graph: Graph) -> None:
        with self._lock:
            self._items[key] = graph
            self._items.move_to_end(key)
            while len(self._items) > self._max:
                self._items.popitem(last=False)


def _load_graph(repo: Repository, cache: GraphCache, tenant_id: str, graph_id: str) -> Graph:
    key = (tenant_id, graph_id)
    cached = cache.get(key)
    if cached is not None:
        return cached
    data = repo.get_graph_data(tenant_id, graph_id)
    if data is None:
        raise JobInputError(f"graph {graph_id} not found")
    graph = Graph.from_dict(data)
    cache.put(key, graph)
    return graph


def solve_request(
    request: OptimizeRequest,
    tenant_id: str,
    repo: Repository,
    cache: GraphCache,
    optimizer: TwoOptFn = _fast.two_opt,
) -> OptimizeResult:
    """Build the distance matrix, solve, and format the result.

    Matrix: O(n^2) in haversine mode, one Dijkstra per distinct snapped node in
    graph mode. Raises ValueError subclasses for problems the caller can fix.
    """
    depot = (request.depot.lat, request.depot.lng)
    stops = [(s.lat, s.lng) for s in request.stops]

    if request.distance_mode == "graph":
        graph = _load_graph(repo, cache, tenant_id, request.graph_id or "")
        nodes = [graph.nearest_node(*depot)] + [graph.nearest_node(*p) for p in stops]
        matrix = graph.cost_matrix(nodes)
    else:
        matrix = distance_matrix([depot, *stops])

    solution = solve_vrp(matrix, depot, stops, request.num_vehicles, optimizer)
    baseline = baseline_distance(matrix, request.num_vehicles)

    routes: list[RouteOut] = []
    for vehicle, (route, length) in enumerate(
        zip(solution.routes, solution.route_lengths, strict=True), start=1
    ):
        route_stops = [
            RouteStop(
                sequence=position,
                id=request.stops[index].id,
                lat=request.stops[index].lat,
                lng=request.stops[index].lng,
            )
            for position, index in enumerate(route, start=1)
        ]
        routes.append(RouteOut(vehicle=vehicle, distance_km=round(length, 3), stops=route_stops))

    total = solution.total_distance
    saved = baseline - total
    percent = (saved / baseline * 100.0) if baseline > 0 else 0.0
    using_cpp = _fast.HAS_CPP and optimizer is _fast.two_opt
    return OptimizeResult(
        distance_mode=request.distance_mode,
        accelerator="cpp" if using_cpp else "python",
        depot=request.depot,
        num_vehicles_used=len(routes),
        total_distance_km=round(total, 3),
        baseline_distance_km=round(baseline, 3),
        saved_km=round(saved, 3),
        saved_percent=round(percent, 2),
        routes=routes,
    )


class JobManager:
    def __init__(
        self,
        repo: Repository,
        settings: Settings,
        optimizer: TwoOptFn | None = None,
    ) -> None:
        self.repo = repo
        self.settings = settings
        self._optimizer: TwoOptFn = optimizer or _fast.two_opt
        self._cache = GraphCache()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, settings.worker_threads), thread_name_prefix="routewise-worker"
        )
        self._pending = 0
        self._lock = threading.Lock()

    @property
    def pending(self) -> int:
        with self._lock:
            return self._pending

    def process(self, tenant_id: str, job_id: str) -> JobRecord | None:
        """Run a queued job in the calling thread and return the final record.

        Only the call that wins the queued -> running claim does the work.
        """
        job = self.repo.mark_running(tenant_id, job_id)
        if job is None:
            return self.repo.get_job(tenant_id, job_id)
        started = time.perf_counter()
        try:
            request = OptimizeRequest.model_validate(job.request)
            result = solve_request(request, tenant_id, self.repo, self._cache, self._optimizer)
            self.repo.complete_job(tenant_id, job_id, result.model_dump(mode="json"))
            status = "succeeded"
        except ValueError as exc:
            self.repo.fail_job(tenant_id, job_id, str(exc)[:500])
            status = "failed"
        except Exception:
            logger.exception("unexpected error", extra={"job_id": job_id, "tenant_id": tenant_id})
            self.repo.fail_job(tenant_id, job_id, "internal error while solving this request")
            status = "failed"
        logger.info(
            "job finished",
            extra={
                "job_id": job_id,
                "tenant_id": tenant_id,
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return self.repo.get_job(tenant_id, job_id)

    def submit(self, tenant_id: str, job_id: str) -> None:
        """Queue a job on the pool; fails the job and raises QueueFullError if full."""
        with self._lock:
            full = self._pending >= MAX_PENDING_JOBS
            if not full:
                self._pending += 1
        if full:
            self.repo.fail_job(tenant_id, job_id, "server is busy; please retry later")
            raise QueueFullError("too many pending jobs")
        try:
            self._executor.submit(self._run_in_pool, tenant_id, job_id)
        except RuntimeError:
            with self._lock:
                self._pending -= 1
            raise

    def _run_in_pool(self, tenant_id: str, job_id: str) -> None:
        try:
            self.process(tenant_id, job_id)
        except Exception:
            logger.exception("worker crashed", extra={"job_id": job_id, "tenant_id": tenant_id})
        finally:
            with self._lock:
                self._pending -= 1

    def recover(self) -> int:
        """Re-queue jobs a previous process left unfinished; returns how many."""
        count = 0
        for tenant_id, job_id in self.repo.requeue_incomplete():
            try:
                self.submit(tenant_id, job_id)
                count += 1
            except QueueFullError:
                logger.warning("recovery queue full", extra={"job_id": job_id})
        return count

    def shutdown(self) -> None:
        """Stop accepting work and wait for running jobs; waiting jobs stay queued in the DB."""
        self._executor.shutdown(wait=True, cancel_futures=True)
