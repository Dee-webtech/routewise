# Architecture

## Components

```text
 Browser (Leaflet)  /  API clients
          |
          v
 api.py        FastAPI app, routes, errors, request IDs, JSON logging
 auth.py       key generation, SHA-256 hashing, header parsing
 ratelimit.py  RateLimiter interface + in-memory token bucket
 jobs.py       solve_request, JobManager (inline + bounded thread pool)
 db.py         Repository: every SQL statement, versioned migrations
 solver.py     nearest-neighbour, 2-opt, Or-opt, sweep, multi-vehicle
 graph.py      road graph, Dijkstra, A*, cost matrix, node snapping
 geo.py        haversine, distance matrix, sweep angle
 _fast.py      picks the C++ 2-opt if built, otherwise the Python one
 cpp/two_opt.cpp   optional pybind11 extension
```

Dependencies point downwards only. `solver`, `graph` and `geo` know nothing about HTTP or the database.

## Lifecycle of POST /api/v1/optimize

1. The request-id middleware assigns or sanitises `X-Request-ID`.
2. The auth dependency resolves the API key to a tenant (hash lookup), then charges the tenant's token bucket (429 when empty).
3. Pydantic validates the body (422), then configured limits are checked (422 `limit_exceeded`).
4. In graph mode, the graph must exist for this tenant (404 otherwise).
5. A job row is inserted. If the `Idempotency-Key` already exists for this tenant, the existing job is returned, or 409 if the body differs (compared by hash).
6. Up to `sync_max_stops` stops: solved inline and returned with 200. More: handed to the thread pool and returned with 202 and a `Location` header.
7. Clients poll `GET /api/v1/jobs/{id}` until `succeeded` or `failed`.

A job that fails because of the input (for example unreachable stops) still returns HTTP 200 with `status: failed` and an `error` message. Unexpected errors are logged with the request context and shown to the client only as "internal error".

## Data model

| Table | Purpose | Notes |
|---|---|---|
| `tenants` | name, `key_hash` (unique), created_at | The plaintext key is never stored |
| `graphs` | tenant_id, node/edge counts, JSON document | Immutable after upload |
| `jobs` | tenant_id, status, request JSON, request hash, idempotency key, result JSON, error | Unique `(tenant_id, idempotency_key)` where the key is not null |

Every tenant-owned query filters on `tenant_id`. Only three methods are cross-tenant: key lookup, tenant listing and crash recovery at startup. Cross-tenant reads therefore look like "not found" (404), not "forbidden", so IDs do not leak.

Schema changes are appended to `MIGRATIONS` and tracked in SQLite's `PRAGMA user_version`.

## Concurrency

- The API process runs synchronous route handlers on FastAPI's thread pool. Large solves run on a separate bounded `ThreadPoolExecutor`, so the API stays responsive.
- A job is claimed with `UPDATE ... WHERE status = 'queued'`, which is atomic, so it can never run twice.
- At startup, `JobManager.recover()` re-queues jobs a crashed process left `queued` or `running`.
- Pending jobs are capped at 1000. Beyond that, the job is failed and the API answers 503.
- Python threads share the GIL, so the pool keeps the API responsive but does not speed up one solve. The C++ 2-opt releases the GIL while it runs.

## Algorithms

| Algorithm | Complexity | Where |
|---|---|---|
| Haversine matrix | O(n^2) | `geo.py` |
| Dijkstra (binary heap) | O((V + E) log V) | `graph.py` |
| A* (haversine heuristic) | O((V + E) log V) worst case, usually far fewer expansions | `graph.py` |
| Cost matrix | O(k (V + E) log V) for k distinct snapped nodes | `graph.py` |
| Nearest-neighbour | O(n^2) | `solver.py` |
| 2-opt | O(n^2) per pass | `solver.py`, `cpp/two_opt.cpp` |
| Or-opt | O(n^2) per pass, move count capped | `solver.py` |
| Sweep assignment | O(n log n) | `solver.py` |

A* is optimal here because uploaded edge weights must be at least the straight-line distance between their endpoints, which makes the haversine heuristic admissible and consistent.

## Observability and operations

- Logs are one JSON object per line, with request ID, method, path, status and duration.
- `/healthz` reports liveness. `/readyz` checks the database and reports the active accelerator.
- OpenAPI docs are served at `/docs`.

## Known limits

See "Limitations" and "Scaling roadmap" in the README.