# ADR 0002: Optional C++ extension for the 2-opt loop

Status: accepted

## Context

Profiling the solver shows that the 2-opt inner loop dominates the time spent improving a tour. It is a tight numeric loop, which is where Python is slowest. Many contributors and users have no C++ compiler, especially on Windows.

## Decision

Implement the 2-opt loop in C++ with pybind11 and declare it as an optional extension in `setup.py`. A failed build prints a warning and `pip install -e .` still succeeds. `_fast.py` exposes a function with the same signature as `solver.two_opt` and uses the C++ version only when the module imports. The C++ code follows the Python scan order, first-improvement rule and epsilon, so both return identical tours. A test asserts this and skips cleanly when the extension is absent. CI builds the extension on Linux and runs that test.

## Consequences

- Nothing in the project requires a compiler. Behaviour is the same with or without the extension.
- Speed-up applies only to 2-opt. Matrix construction and Or-opt stay in Python.
- Each C++ call converts the Python matrix into a flat C++ array, which costs O(n^2) per call. The benchmark includes this cost.
- Two implementations must be kept in step. The equivalence test is the guard.
- `ROUTEWISE_SKIP_CPP=1` skips the build, which CI uses to test the pure-Python path.

## Alternatives considered

- **NumPy or Numba:** would speed up other parts too, but adds heavy dependencies and moves away from hand-written algorithms.
- **A required compiled dependency:** faster everywhere, but breaks installs on machines without a compiler.