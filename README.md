# RouteWise

![CI](https://github.com/Dee-webtech/routewise/actions/workflows/ci.yml/badge.svg)

Multi-tenant route optimisation API. A business submits a depot and a list of stops and gets back the best order of stops per vehicle, the total distance, and how much shorter that is than its original order.

## Problem and audience

Small delivery businesses (food vendors, couriers, pharmacies) often plan multi-stop routes by hand because commercial route-optimisation APIs are expensive. RouteWise is a small, self-hostable alternative. Its algorithms are written by hand, with no routing libraries.

## Features

- Multi-tenant API keys, stored only as SHA-256 hashes, with every row scoped by `tenant_id`
- `POST /api/v1/optimize`: solved immediately when small, queued as a job (HTTP 202) when large
- `Idempotency-Key` support, so client retries never create duplicate jobs
- Road-network mode: upload a graph, query shortest paths with Dijkstra or A*, optimise over road distances
- Hand-written haversine, Dijkstra (binary heap), A*, nearest-neighbour, 2-opt, Or-opt and sweep-based multi-vehicle assignment
- Optional C++ 2-opt accelerator with automatic fallback to pure Python
- Single-page Leaflet front end at `/`
- `/healthz`, `/readyz`, JSON logs, request IDs, per-tenant rate limiting, consistent error responses, OpenAPI docs at `/docs`
- SQLite (WAL) behind a repository layer, with versioned migrations

## Architecture

```text
 Browser (Leaflet)  /  curl clients
          |
          v
 FastAPI app (api.py)
   middleware : request id + JSON access log
   dependency : API key -> tenant -> rate limit
   routes     : /optimize  /jobs  /graphs  /healthz  /readyz  /
          |                          |
          v                          v
 JobManager (jobs.py)          Repository (db.py)
   small  -> solved inline       SQLite, WAL mode
   large  -> bounded thread      tenants | graphs | jobs
             pool, state in DB
          |
          v
 solver.py + graph.py + geo.py       (pure Python)
          |
          v
 _fast.py -> optional C++ 2-opt (_cpp_two_opt)
```

More detail is in [docs/architecture.md](docs/architecture.md).

## Quick start (60 seconds)

Requires Python 3.10 or newer. No C++ compiler is needed.

```bash
git clone https://github.com/Dee-webtech/routewise.git
cd routewise
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash. On macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m routewise.cli create-tenant "Demo Bakery"
python -m uvicorn routewise.api:create_app --factory --port 8000
```

Open http://127.0.0.1:8000, paste the API key, click the map once for the depot, click more points for stops, then press **Optimise**. API docs are at http://127.0.0.1:8000/docs.

## Create a tenant

```bash
python -m routewise.cli create-tenant "Acme Couriers"
python -m routewise.cli list-tenants
```

The key is printed once. Only its SHA-256 hash is stored, so a lost key cannot be recovered. Create a new tenant instead.

## API examples

```bash
KEY="rw_your_key_here"
BASE="http://127.0.0.1:8000"

# Optimise (idempotent when you send an Idempotency-Key)
curl -s -X POST "$BASE/api/v1/optimize" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" -H "Idempotency-Key: order-1001" \
  -d '{"depot":{"lat":6.5,"lng":3.4},"num_vehicles":2,"stops":[
        {"id":"a","lat":6.52,"lng":3.42},{"id":"b","lat":6.51,"lng":3.38},
        {"id":"c","lat":6.55,"lng":3.41},{"id":"d","lat":6.47,"lng":3.45}]}'

# Fetch a job (queued jobs return 202 and a Location header)
curl -s -H "X-API-Key: $KEY" "$BASE/api/v1/jobs/JOB_ID"

# Upload a road graph, then query it
python scripts/make_city_graph.py --size 20 --out city_graph.json
curl -s -X POST "$BASE/api/v1/graphs" -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  --data @city_graph.json
curl -s -H "X-API-Key: $KEY" "$BASE/api/v1/graphs/GRAPH_ID/path?from=0&to=399&algo=astar"

# Optimise over the road network (use stops inside the graph's area)
curl -s -X POST "$BASE/api/v1/optimize" -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"depot":{"lat":6.41,"lng":3.31},"distance_mode":"graph","graph_id":"GRAPH_ID","stops":[
        {"id":"a","lat":6.44,"lng":3.33},{"id":"b","lat":6.43,"lng":3.36},{"id":"c","lat":6.45,"lng":3.34}]}'
```

Errors share one shape: `{"error": {"code", "message", "request_id"}}`.

To use real streets, export a bounding box from [Overpass Turbo](https://overpass-turbo.eu) with the query below (Export, raw OSM data as JSON), then convert it:

```text
[out:json][timeout:60];
way["highway"](SOUTH,WEST,NORTH,EAST);
(._;>;);
out body;
```

```bash
python scripts/osm_to_graph.py export.json --out graph.json --name "My district"
```

## Run the tests

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

## Optional C++ accelerator

The 2-opt inner loop has a C++ version (`cpp/two_opt.cpp`, via pybind11). If it is not built, RouteWise silently uses the pure-Python version, which returns identical tours.

```bash
python -m pip install pybind11
python setup.py build_ext --inplace
python -c "from routewise import _fast; print('C++ built:', _fast.HAS_CPP)"
```

This needs a C++17 compiler (GCC, Clang or MSVC). GitHub Actions builds and tests it on Linux. Set `ROUTEWISE_SKIP_CPP=1` to skip the build entirely.

## Benchmarks

Generate the real numbers on your machine with `python scripts/update_readme_bench.py`. The section below is rewritten in place, and the same report is saved to [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

<!-- BENCH:START -->
Python 3.13.3 on Windows AMD64. C++ extension built: no.

### 2-opt local search (nearest-neighbour start, best of 3 runs)

| Stops | Python (ms) | C++ (ms) | Speedup | Identical tours |
|---:|---:|---:|---:|:---:|
| 100 | 4.8 | not built | n/a | n/a |
| 200 | 19.4 | not built | n/a | n/a |
| 400 | 111.6 | not built | n/a | n/a |

The C++ timings include converting the Python distance matrix on every call.

### Dijkstra vs A* (30x30 synthetic city grid)

| Nodes | Queries | Dijkstra expanded (avg) | A* expanded (avg) | A* / Dijkstra | Dijkstra (ms/query) | A* (ms/query) | Equal costs |
|---:|---:|---:|---:|---:|---:|---:|:---:|
| 900 | 100 | 423.0 | 164.4 | 38.9% | 0.764 | 0.828 | yes |

Timings depend on the machine. Node counts do not.
<!-- BENCH:END -->

The method behind the C++ accelerator is written up in [docs/case-studies/01-two-opt-hot-loop.md](docs/case-studies/01-two-opt-hot-loop.md).

## Configuration

All settings are environment variables, read when the app starts.

| Variable | Default | Meaning |
|---|---|---|
| `ROUTEWISE_DB_PATH` | `routewise.db` | SQLite file |
| `ROUTEWISE_SYNC_MAX_STOPS` | `50` | More stops than this are queued (HTTP 202) |
| `ROUTEWISE_MAX_STOPS` | `2000` | Hard limit on stops per request |
| `ROUTEWISE_MAX_VEHICLES` | `50` | Hard limit on vehicles per request |
| `ROUTEWISE_WORKER_THREADS` | `2` | Background worker threads |
| `ROUTEWISE_RATE_LIMIT_PER_MINUTE` | `120` | Sustained requests per minute per tenant |
| `ROUTEWISE_RATE_LIMIT_BURST` | `20` | Burst capacity per tenant |
| `ROUTEWISE_MAX_GRAPH_NODES` | `200000` | Largest accepted road graph |
| `ROUTEWISE_LOG_LEVEL` | `INFO` | Log level |

## Docker

```bash
docker compose up --build
docker compose exec routewise python -m routewise.cli create-tenant "Demo Bakery"
```

Data lives in a named volume. Run a single container: the rate limiter and job pool are per process.

## Design decisions

- **Heuristics, not exact solving.** Nearest-neighbour, then 2-opt and Or-opt. Results are good, not provably optimal. See [ADR 0003](docs/adr/0003-sweep-then-route.md).
- **Sweep, then route** for multiple vehicles: simple and fast, but it ignores capacity and time windows.
- **SQLite behind a repository.** Zero setup, and replaceable. See [ADR 0001](docs/adr/0001-sqlite-behind-repository.md).
- **Optional C++.** Nothing depends on a compiler. See [ADR 0002](docs/adr/0002-optional-cpp-extension.md).
- **Admissible A\*.** Graph edge weights must be at least the straight-line distance, and uploads that break this are rejected.
- **Honest savings figure.** `saved_percent` is measured against the stops in the order you sent them, and it can be negative if your order was already better.

## Limitations

- The rate limiter is in memory. With several processes each one enforces its own limit, and buckets reset on restart.
- SQLite allows one writer at a time, and jobs run in the API process. Run one process.
- Jobs and results are kept forever. There is no expiry and no API key rotation.
- Capacities, time windows and traffic are not modelled. Distances are straight-line (haversine) or from your graph.
- 2-opt and Or-opt assume symmetric distances. With one-way roads the solver never returns a tour worse than its nearest-neighbour start, but quality can drop.
- The sweep does not handle the antimeridian, and nearest-node snapping is a linear scan.
- There is no TLS. Put a reverse proxy in front.

## Scaling roadmap

1. Postgres via a second `Repository` implementation.
2. Redis-backed rate limiting behind the existing `RateLimiter` interface.
3. A real queue (Redis or a database-backed queue) with separate worker processes.
4. Several API replicas behind a load balancer once steps 1 to 3 are done.
5. Spatial index for node snapping, and graph preprocessing for large road networks.

## Git workflow

Every change goes feature branch, then PR into `develop`, `testing`, `staging` and finally `main`, with CI required at each step. Commits follow [Conventional Commits](https://www.conventionalcommits.org) (`feat:`, `fix:`, `docs:`, `test:`, `ci:`). Releases are tagged on `main`.

## License

MIT