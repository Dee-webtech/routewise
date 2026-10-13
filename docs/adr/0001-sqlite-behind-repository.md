# ADR 0001: SQLite behind a repository layer

Status: accepted

## Context

RouteWise needs durable storage for tenants, graphs and jobs. It must run with zero setup on a laptop, in CI and in a single container, yet not be locked into one database.

## Decision

Use SQLite from the standard library in WAL mode, accessed only through the `Repository` class in `db.py`. No SQL appears anywhere else. Every operation opens a short-lived connection, so no connection object is shared between threads. The schema is created by a versioned `MIGRATIONS` list tracked in `PRAGMA user_version`.

## Consequences

- No server to install, and tests run against real files in temporary directories.
- WAL allows concurrent readers alongside one writer. Write throughput is limited to one writer at a time.
- A file-based database can only be used by processes on the same machine, so horizontal scaling needs a different store.
- Moving to Postgres means writing another class with the same methods and a migration runner. Routes, jobs and tests only depend on the `Repository` method contract.
- `:memory:` is not supported because each connection would see a separate database.

## Alternatives considered

- **Postgres from day one:** more realistic for production, but it adds setup cost and a service dependency for a portfolio-sized product.
- **An ORM:** extra dependency and indirection for three small tables.