# RouteWise: project hand-off

## What it is, in plain words

Imagine a baker with 20 deliveries and two drivers. Today someone plans the order by hand, and the drivers zigzag across town. RouteWise takes the bakery's location and the list of addresses, and returns a better order for each driver. It also says how much shorter the new plan is than the original order.

Commercial route-planning services are expensive for small businesses. RouteWise is a small version that anyone can run themselves.

## How it works, step by step

1. **Each business gets its own key.** Like a locker key, it opens only that business's data. I store only a scrambled version of the key, so even I can't read it back.
2. **The business sends the depot and stops** through the web page or the API.
3. **The system measures distances**, either in a straight line or along a road map the business uploads.
4. **It picks a good order.**
   - *Nearest neighbour:* always drive to the closest stop that's left.
   - *2-opt:* find two paths that cross and untangle them.
   - *Or-opt:* move a stop to a better place in the route.
   - *Sweep:* with several drivers, split the stops by compass direction around the depot, so each driver covers a slice.
5. **It reports the result:** the order per driver, total distance, and percent shorter than the original order. If the original order was already better, it says so rather than hiding it.
6. **Small requests are answered immediately.** Big ones get a ticket number and run in the background, and the page checks the ticket until the result is ready.
7. **Retries are safe.** If a client resends the same request by accident, it gets the same answer and no duplicate job.

## The speed-up

The most repeated part of the solver is the untangling step. I rewrote that one part in C++ as an optional add-on. If the machine has no C++ compiler, the program quietly uses the Python version, which gives the same answers. A test checks that both give identical results. The measured numbers are in `docs/BENCHMARKS.md`. I generate them with a script and never type them by hand. The C++ build runs on GitHub's Linux machines. My Windows machine runs the pure-Python version.

## 30-second pitch

"Small delivery businesses plan routes by hand because routing APIs are costly. I built a multi-tenant route-optimisation service in Python and FastAPI. The core algorithms are hand-written with no routing libraries: Dijkstra, A*, 2-opt and Or-opt. It has per-business API keys, background jobs, safe retries, a map UI, CI on three Python versions, and an optional C++ accelerator. I profiled before optimising and wrote up the method."

## Tech summary

Python 3.10+, FastAPI, Pydantic v2, SQLite (WAL) behind a repository layer, pytest, ruff, optional C++ via pybind11, GitHub Actions, Docker. The tests cover the algorithms, tenant isolation, idempotency, job lifecycle, validation and rate limiting.

## Honest limitations

- The rate limiter lives in one process's memory, so it works for a single server only.
- SQLite supports one writer at a time, and jobs run inside the API process.
- There are no vehicle capacities or time windows, and no live traffic.
- The multi-vehicle split is by direction and stop count. It is fast, but not optimal.
- There is no HTTPS, so it needs a reverse proxy in front.

## What I would build next

Postgres, Redis for rate limiting and the job queue, separate worker processes, and capacity and time-window constraints.

## How I worked: Git

Every change went from a feature branch through four pull requests: develop, testing, staging, main. CI ran at each step, and commits follow Conventional Commits.

## Problems I hit and how I fixed them

| Problem | Cause | Fix |
|---|---|---|
| Red squiggles in the C++ file in VS Code | The editor couldn't find C++ headers because I have no local compiler. Not a code error. | Ignored them, or turned off the C++ squiggles for the workspace. |
| `No module named ruff` | The virtual environment wasn't active. | Ran `source .venv/Scripts/activate`, then `pip install -e ".[dev]"`. |
| Code was too heavily commented | Over-explaining in the first version. | Asked for cleaner code and trimmed comments to short docstrings. |
| GitHub compare page said "nothing to compare" | I picked the wrong branch pair. | Learned that base is the receiving branch and compare is the one holding the changes. |
| Files showed "4 days ago" | GitHub shows the last commit date, not the push date. | Nothing to fix. |
| API tests were missing (78 tests passing) | `test_api.py` and the updated `conftest.py` were never pasted. | Added them, which brought the total to 103 passing. |
| I ticked "auto-delete branches" and removed promotion branches | A repo setting, plus deleting source branches after PRs. | Unticked the setting and recreated develop, testing and staging from main. |
| Test warning about httpx | A harmless deprecation notice from third-party packages. | Left it alone. |

Test counts over time: 75, then 78, then 103, then 109 passing, with 2 skipped locally because the C++ extension isn't built here.

## Questions I can expect

- **Why SQLite?** It needs no setup and sits behind a repository class, so Postgres can replace it (ADR 0001).
- **Why is C++ optional?** Nothing should depend on having a compiler (ADR 0002).
- **Is the A* result always optimal?** Yes here, because uploaded road lengths must be at least the straight-line distance, which keeps the heuristic admissible.
- **Why can the savings be negative?** It compares against the order the customer sent, and sometimes that order was already better than the heuristic result.