"""SQLite repository layer. All SQL in the project lives here.

Every tenant-owned query filters on tenant_id. The only cross-tenant methods are
get_tenant_by_key_hash, list_tenants and requeue_incomplete. Each operation uses
its own short-lived WAL connection, so a file path is required (not ":memory:").
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"

# Versioned migrations. Append new (version, sql) pairs; never edit old ones.
# The applied version is stored in SQLite's PRAGMA user_version.
MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    key_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS graphs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants (id),
    name TEXT,
    node_count INTEGER NOT NULL,
    edge_count INTEGER NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_graphs_tenant ON graphs (tenant_id);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants (id),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    request TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    idempotency_key TEXT,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_tenant_idem
    ON jobs (tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_jobs_tenant_created ON jobs (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status);
""",
    ),
]

_JOB_COLUMNS = (
    "id, tenant_id, status, request, request_hash, idempotency_key, "
    "result, error, created_at, updated_at"
)


class DuplicateTenantError(Exception):
    """Raised when a tenant name already exists."""


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    created_at: str


@dataclass(frozen=True)
class GraphRecord:
    id: str
    tenant_id: str
    name: str | None
    node_count: int
    edge_count: int
    created_at: str


@dataclass(frozen=True)
class JobRecord:
    id: str
    tenant_id: str
    status: str
    request: dict[str, Any]
    request_hash: str
    idempotency_key: str | None
    result: dict[str, Any] | None
    error: str | None
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every migration newer than the database's version. Returns the version.

    Run this once at startup. Each migration runs in its own transaction, and
    the statements use IF NOT EXISTS so two processes starting together are safe.
    """
    version = current_version(conn)
    for target, script in MIGRATIONS:
        if target <= version:
            continue
        conn.executescript(
            f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version = {int(target)};\nCOMMIT;"
        )
        version = target
    return version


def _job_from_row(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        status=row["status"],
        request=json.loads(row["request"]),
        request_hash=row["request_hash"],
        idempotency_key=row["idempotency_key"],
        result=json.loads(row["result"]) if row["result"] is not None else None,
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class Repository:
    def __init__(self, path: str) -> None:
        self.path = path

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA synchronous = NORMAL")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ setup
    def init_schema(self) -> int:
        """Create the database file if needed, enable WAL, run migrations."""
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            return migrate(conn)

    def ping(self) -> bool:
        """True if the database answers a trivial query (used by /readyz)."""
        with self._conn() as conn:
            conn.execute("SELECT 1").fetchone()
        return True

    # --------------------------------------------------------------- tenants
    def create_tenant(self, name: str, key_hash: str) -> Tenant:
        tenant = Tenant(id=_new_id(), name=name, created_at=_now())
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO tenants (id, name, key_hash, created_at) VALUES (?, ?, ?, ?)",
                    (tenant.id, tenant.name, key_hash, tenant.created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateTenantError(f"tenant {name!r} already exists") from exc
        return tenant

    def get_tenant_by_key_hash(self, key_hash: str) -> Tenant | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, name, created_at FROM tenants WHERE key_hash = ?", (key_hash,)
            ).fetchone()
        if row is None:
            return None
        return Tenant(id=row["id"], name=row["name"], created_at=row["created_at"])

    def list_tenants(self) -> list[Tenant]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at FROM tenants ORDER BY created_at, name"
            ).fetchall()
        return [Tenant(id=r["id"], name=r["name"], created_at=r["created_at"]) for r in rows]

    # ---------------------------------------------------------------- graphs
    def create_graph(
        self,
        tenant_id: str,
        name: str | None,
        data: dict[str, Any],
        node_count: int,
        edge_count: int,
    ) -> GraphRecord:
        record = GraphRecord(
            id=_new_id(),
            tenant_id=tenant_id,
            name=name,
            node_count=node_count,
            edge_count=edge_count,
            created_at=_now(),
        )
        payload = json.dumps(data, separators=(",", ":"))
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO graphs (id, tenant_id, name, node_count, edge_count, data, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    tenant_id,
                    name,
                    node_count,
                    edge_count,
                    payload,
                    record.created_at,
                ),
            )
        return record

    def get_graph(self, tenant_id: str, graph_id: str) -> GraphRecord | None:
        """Graph metadata (without the potentially large node/edge data)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, tenant_id, name, node_count, edge_count, created_at"
                " FROM graphs WHERE tenant_id = ? AND id = ?",
                (tenant_id, graph_id),
            ).fetchone()
        if row is None:
            return None
        return GraphRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            node_count=row["node_count"],
            edge_count=row["edge_count"],
            created_at=row["created_at"],
        )

    def get_graph_data(self, tenant_id: str, graph_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM graphs WHERE tenant_id = ? AND id = ?", (tenant_id, graph_id)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["data"])

    # ------------------------------------------------------------------ jobs
    def create_job(
        self,
        tenant_id: str,
        request: dict[str, Any],
        request_hash: str,
        idempotency_key: str | None,
    ) -> tuple[JobRecord, bool]:
        """Insert a queued job. Returns (job, created).

        If ``idempotency_key`` already exists for this tenant, nothing is
        inserted and the EXISTING job is returned with ``created=False``.
        """
        job_id = _new_id()
        now = _now()
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO jobs (id, tenant_id, status, request, request_hash,"
                    " idempotency_key, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        tenant_id,
                        QUEUED,
                        json.dumps(request, separators=(",", ":")),
                        request_hash,
                        idempotency_key,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            if idempotency_key is None:
                raise
            existing = self._get_job_by_key(tenant_id, idempotency_key)
            if existing is None:
                raise
            return existing, False
        job = self.get_job(tenant_id, job_id)
        if job is None:  # pragma: no cover - the row was just inserted
            raise RuntimeError("job vanished after insert")
        return job, True

    def get_job(self, tenant_id: str, job_id: str) -> JobRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {_JOB_COLUMNS} FROM jobs WHERE tenant_id = ? AND id = ?",
                (tenant_id, job_id),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def _get_job_by_key(self, tenant_id: str, idempotency_key: str) -> JobRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {_JOB_COLUMNS} FROM jobs WHERE tenant_id = ? AND idempotency_key = ?",
                (tenant_id, idempotency_key),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def mark_running(self, tenant_id: str, job_id: str) -> JobRecord | None:
        """Move queued -> running. Returns the job, or None if it was not queued.

        The status check in the UPDATE makes this an atomic claim: two workers
        can never both start the same job.
        """
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ?"
                " WHERE tenant_id = ? AND id = ? AND status = ?",
                (RUNNING, _now(), tenant_id, job_id, QUEUED),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute(
                f"SELECT {_JOB_COLUMNS} FROM jobs WHERE tenant_id = ? AND id = ?",
                (tenant_id, job_id),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def complete_job(self, tenant_id: str, job_id: str, result: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, result = ?, error = NULL, updated_at = ?"
                " WHERE tenant_id = ? AND id = ?",
                (SUCCEEDED, json.dumps(result, separators=(",", ":")), _now(), tenant_id, job_id),
            )

    def fail_job(self, tenant_id: str, job_id: str, error: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ?"
                " WHERE tenant_id = ? AND id = ?",
                (FAILED, error, _now(), tenant_id, job_id),
            )

    def requeue_incomplete(self) -> list[tuple[str, str]]:
        """Crash recovery: running -> queued, then list every queued (tenant_id, job_id).

        Cross-tenant by design; only called once at startup by JobManager.recover().
        """
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE status = ?",
                (QUEUED, _now(), RUNNING),
            )
            rows = conn.execute(
                "SELECT tenant_id, id FROM jobs WHERE status = ? ORDER BY created_at", (QUEUED,)
            ).fetchall()
        return [(r["tenant_id"], r["id"]) for r in rows]
