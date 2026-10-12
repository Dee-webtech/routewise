"""FastAPI application: routes, errors, request IDs and JSON logging."""

from __future__ import annotations

import json
import logging
import math
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, _fast
from .auth import authenticate, extract_api_key
from .config import Settings, load_settings
from .db import GraphRecord, JobRecord, Repository, Tenant
from .graph import Graph, GraphError
from .jobs import (
    GraphCache,
    JobInputError,
    JobManager,
    QueueFullError,
    load_graph,
    request_fingerprint,
)
from .ratelimit import TokenBucketLimiter
from .schemas import (
    ErrorDetail,
    ErrorResponse,
    GraphOut,
    GraphUpload,
    JobOut,
    OptimizeRequest,
    OptimizeResult,
    PathOut,
)

logger = logging.getLogger("routewise.api")

STATIC_DIR = Path(__file__).parent / "static"
HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
}
AUTH_ERRORS = {401: {"model": ErrorResponse}, 429: {"model": ErrorResponse}}
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_LOG_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update({k: v for k, v in record.__dict__.items() if k not in _LOG_RESERVED})
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("routewise")
    root.handlers = [handler]
    root.setLevel(level)
    root.propagate = False


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


def _error(
    request: Request, status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(
            code=code, message=message, request_id=getattr(request.state, "request_id", None)
        )
    )
    return JSONResponse(body.model_dump(), status_code=status, headers=headers)


async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return _error(request, exc.status, exc.code, exc.message, exc.headers)


async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = HTTP_CODES.get(exc.status_code, "http_error")
    headers = dict(exc.headers) if exc.headers else None
    return _error(request, exc.status_code, code, str(exc.detail), headers)


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    problems = []
    for item in exc.errors()[:5]:
        where = ".".join(str(part) for part in item["loc"])
        problems.append(f"{where}: {item['msg']}")
    return _error(request, 422, "validation_error", "; ".join(problems))


async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled error", exc_info=exc)
    return _error(request, 500, "internal_error", "internal server error")


async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    supplied = request.headers.get("x-request-id", "")
    request_id = supplied if _REQUEST_ID_RE.fullmatch(supplied) else uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )
    return response


def current_tenant(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Tenant:
    state = request.app.state
    tenant = authenticate(state.repo, extract_api_key(x_api_key, authorization))
    if tenant is None:
        raise ApiError(
            401, "unauthorized", "missing or invalid API key", {"WWW-Authenticate": "Bearer"}
        )
    decision = state.limiter.check(tenant.id)
    if not decision.allowed:
        raise ApiError(
            429,
            "rate_limited",
            "rate limit exceeded; retry later",
            {
                "Retry-After": str(max(1, math.ceil(decision.retry_after))),
                "X-RateLimit-Limit": str(decision.limit),
            },
        )
    return tenant


TenantDep = Annotated[Tenant, Depends(current_tenant)]
router = APIRouter()


def _job_out(job: JobRecord) -> JobOut:
    result = OptimizeResult.model_validate(job.result) if job.result is not None else None
    return JobOut(
        id=job.id,
        status=job.status,
        created_at=job.created_at,
        updated_at=job.updated_at,
        result=result,
        error=job.error,
    )


def _graph_out(record: GraphRecord) -> GraphOut:
    return GraphOut(
        id=record.id,
        name=record.name,
        node_count=record.node_count,
        edge_count=record.edge_count,
        created_at=record.created_at,
    )


def _clean_key(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not 1 <= len(value) <= 128:
        raise ApiError(
            400, "invalid_idempotency_key", "Idempotency-Key must be 1 to 128 characters"
        )
    return value


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@router.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["ops"])
def readyz(request: Request) -> dict[str, str]:
    try:
        request.app.state.repo.ping()
    except Exception as exc:
        logger.error("readiness check failed", extra={"error": str(exc)})
        raise ApiError(503, "not_ready", "database is not reachable") from exc
    return {"status": "ready", "accelerator": "cpp" if _fast.HAS_CPP else "python"}


@router.post(
    "/api/v1/optimize",
    tags=["optimize"],
    responses={
        **AUTH_ERRORS,
        202: {"model": JobOut, "description": "Queued; poll the Location URL"},
    },
)
def optimize(
    body: OptimizeRequest,
    request: Request,
    response: Response,
    tenant: TenantDep,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JobOut:
    state = request.app.state
    settings: Settings = state.settings
    try:
        body.check_limits(settings.max_stops, settings.max_vehicles)
    except ValueError as exc:
        raise ApiError(422, "limit_exceeded", str(exc)) from exc
    key = _clean_key(idempotency_key)
    if body.graph_id and state.repo.get_graph(tenant.id, body.graph_id) is None:
        raise ApiError(404, "graph_not_found", f"graph {body.graph_id} not found")

    fingerprint = request_fingerprint(body)
    job, created = state.repo.create_job(tenant.id, body.model_dump(mode="json"), fingerprint, key)
    if not created:
        if job.request_hash != fingerprint:
            raise ApiError(
                409,
                "idempotency_conflict",
                "this Idempotency-Key was already used with a different request body",
            )
    elif len(body.stops) > settings.sync_max_stops:
        try:
            state.manager.submit(tenant.id, job.id)
        except QueueFullError as exc:
            raise ApiError(
                503, "busy", "server is busy; please retry later", {"Retry-After": "5"}
            ) from exc
    else:
        job = state.manager.process(tenant.id, job.id) or job

    if job.status in ("succeeded", "failed"):
        response.status_code = 200
    else:
        response.status_code = 202
        response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return _job_out(job)


@router.get("/api/v1/jobs/{job_id}", tags=["optimize"], responses=AUTH_ERRORS)
def get_job(job_id: str, request: Request, tenant: TenantDep) -> JobOut:
    job = request.app.state.repo.get_job(tenant.id, job_id)
    if job is None:
        raise ApiError(404, "job_not_found", f"job {job_id} not found")
    return _job_out(job)


@router.post("/api/v1/graphs", status_code=201, tags=["graphs"], responses=AUTH_ERRORS)
def upload_graph(body: GraphUpload, request: Request, tenant: TenantDep) -> GraphOut:
    state = request.app.state
    document = {"nodes": body.nodes, "edges": body.edges}
    try:
        graph = Graph.from_dict(document, max_nodes=state.settings.max_graph_nodes)
    except GraphError as exc:
        raise ApiError(422, "invalid_graph", str(exc)) from exc
    record = state.repo.create_graph(
        tenant.id, body.name, document, graph.node_count, len(body.edges)
    )
    state.graph_cache.put((tenant.id, record.id), graph)
    return _graph_out(record)


@router.get("/api/v1/graphs/{graph_id}", tags=["graphs"], responses=AUTH_ERRORS)
def get_graph(graph_id: str, request: Request, tenant: TenantDep) -> GraphOut:
    record = request.app.state.repo.get_graph(tenant.id, graph_id)
    if record is None:
        raise ApiError(404, "graph_not_found", f"graph {graph_id} not found")
    return _graph_out(record)


@router.get("/api/v1/graphs/{graph_id}/path", tags=["graphs"], responses=AUTH_ERRORS)
def graph_path(
    graph_id: str,
    request: Request,
    tenant: TenantDep,
    from_node: Annotated[int, Query(alias="from")],
    to_node: Annotated[int, Query(alias="to")],
    algo: Literal["dijkstra", "astar"] = "dijkstra",
) -> PathOut:
    state = request.app.state
    try:
        graph = load_graph(state.repo, state.graph_cache, tenant.id, graph_id)
    except JobInputError as exc:
        raise ApiError(404, "graph_not_found", str(exc)) from exc
    try:
        result = graph.shortest_path(from_node, to_node, algo)
    except GraphError as exc:
        raise ApiError(404, "node_not_found", str(exc)) from exc
    if result is None:
        raise ApiError(404, "no_path", "no path exists between these nodes")
    return PathOut(
        from_node=from_node,
        to_node=to_node,
        algo=algo,
        cost_km=round(result.cost, 3),
        path=result.path,
        nodes_expanded=result.expanded,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.manager.recover()
    yield
    app.state.manager.shutdown()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)
    repo = Repository(settings.db_path)
    repo.init_schema()

    app = FastAPI(
        title="RouteWise",
        version=__version__,
        description="Multi-tenant route optimisation API.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.repo = repo
    app.state.manager = JobManager(repo, settings)
    app.state.limiter = TokenBucketLimiter(
        settings.rate_limit_per_minute, settings.rate_limit_burst
    )
    app.state.graph_cache = GraphCache()

    app.middleware("http")(request_context)
    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected)
    app.include_router(router)
    return app
