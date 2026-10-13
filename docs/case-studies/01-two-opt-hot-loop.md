# Case study 1: finding and fixing the 2-opt hot loop

This is the method, not a victory lap. All timings live in [../BENCHMARKS.md](../BENCHMARKS.md), which `scripts/update_readme_bench.py` generates from real runs, so this page never contains numbers that could go stale.

## 1. Profile first

Before changing anything, measure where the time goes. With the C++ extension not built, profile the benchmark:

```bash
mkdir -p benchmarks/results
python -m cProfile -o benchmarks/results/profile.out benchmarks/bench_solver.py --quick
python -c "import pstats; pstats.Stats('benchmarks/results/profile.out').sort_stats('tottime').print_stats(8)"
```

`tottime` counts time spent inside a function itself, excluding callees. That is the number that points at a hot loop.

## 2. Identify the hot loop

In the profile, `solver.two_opt` should stand out in `tottime`. It is a doubly nested loop that runs O(n^2) pair checks per pass, and each check does four matrix lookups and a few additions. That is pure interpreter overhead, which is exactly what Python is slow at. Record what you see for your own machine and size.

## 3. Rewrite only that loop

`cpp/two_opt.cpp` re-implements this one function with the same scan order, first-improvement rule, epsilon and fixed depot. Decisions that kept the change small:

- Only the hot loop moved. Matrix construction, Or-opt and the API stay in Python.
- The matrix is flattened once per call so the loop reads contiguous memory.
- The extension is optional (see ADR 0002), and `_fast.py` falls back automatically.

## 4. Re-measure

```bash
python -m pip install pybind11
python setup.py build_ext --inplace
python benchmarks/bench_solver.py
```

The 2-opt table shows both timings, the speed-up and whether the tours are identical. Run it more than once, and compare like with like (same sizes, same seeds, same machine).

## 5. Verify equal output

A faster function that returns different answers is a regression. `tests/test_fast.py` asserts that the C++ and Python versions return identical tours, and therefore equal lengths, on the same seeded inputs, and that the C++ side rejects out-of-range indices. The benchmark's "Identical tours" column repeats the check on every run. CI builds the extension on Linux and fails if the test is skipped.

## What to expect, and what not to

- The speed-up applies to the 2-opt stage only. The overall request time also includes the matrix, Or-opt and JSON handling, which this change does not touch, so the end-to-end gain is smaller than the 2-opt gain.
- Each call converts a Python list-of-lists into C++ vectors. That conversion is part of the measured C++ time, and it is why small inputs gain less.
- Timings differ per machine. Compare ratios, not absolute values.

## Takeaways

1. Measure, then optimise the measured bottleneck.
2. Keep the fast path optional and behaviourally identical.
3. Guard it with a test that compares outputs, and with a benchmark that reports them.