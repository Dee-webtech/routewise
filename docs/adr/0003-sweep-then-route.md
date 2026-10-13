# ADR 0003: Sweep, then route, for multiple vehicles

Status: accepted

## Context

Customers may have several vehicles. Exact multi-vehicle routing is NP-hard and needs heavy solvers. RouteWise targets small fleets and needs fast, predictable answers.

## Decision

Assign stops to vehicles by angle around the depot (a sweep), cutting the angle-sorted list into near-equal groups. Then solve each vehicle's route independently: nearest-neighbour, 2-opt, Or-opt, 2-opt again. The result is never longer than the nearest-neighbour tour it started from.

## Consequences

- Fast (O(n log n) assignment) and deterministic, with one balanced group per vehicle.
- Routes rarely cross each other, which is good for real drivers.
- Balance is by stop count, not by distance, load or time. There are no capacities or time windows.
- Depots at the edge of the service area, or very uneven stop density, give weaker partitions than a proper vehicle-routing solver.
- The "original order" baseline splits the stops into contiguous chunks in the order sent, one chunk per vehicle. The savings figure compares against that, and it can be negative.

## Alternatives considered

- **Clarke-Wright savings or other VRP heuristics:** better partitions, but more code and more tuning.
- **Exact or metaheuristic solvers (for example OR-Tools):** stronger, but they are routing libraries, and the aim here is hand-written algorithms.