import hashlib
import sqlite3
from pathlib import Path

import pytest

from routewise.auth import (
    authenticate,
    create_tenant,
    extract_api_key,
    generate_api_key,
    hash_api_key,
    validate_tenant_name,
)
from routewise.db import MIGRATIONS, DuplicateTenantError

SMALL_GRAPH = {
    "nodes": [{"id": 1, "lat": 0.0, "lng": 0.0}, {"id": 2, "lat": 0.0, "lng": 0.01}],
    "edges": [{"from": 1, "to": 2, "weight": 1.2}],
}


def test_schema_is_created_at_the_latest_version_in_wal_mode(repo, settings):
    conn = sqlite3.connect(settings.db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert version == len(MIGRATIONS)
    assert {"tenants", "graphs", "jobs"} <= tables
    assert mode.lower() == "wal"


def test_init_schema_is_idempotent(repo):
    assert repo.init_schema() == len(MIGRATIONS)
    assert repo.init_schema() == len(MIGRATIONS)


def test_ping(repo):
    assert repo.ping() is True


def test_generated_keys_are_unique_and_prefixed():
    first = generate_api_key()
    second = generate_api_key()
    assert first != second
    assert first.startswith("rw_")
    assert len(hash_api_key(first)) == 64


def test_api_key_is_stored_only_as_a_sha256_hash(repo, settings):
    tenant, key = create_tenant(repo, "Acme")
    assert tenant.name == "Acme"
    conn = sqlite3.connect(settings.db_path)
    try:
        rows = conn.execute("SELECT * FROM tenants").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    flat = " ".join(str(value) for value in rows[0])
    assert key not in flat
    assert hashlib.sha256(key.encode("utf-8")).hexdigest() in flat
    for suffix in ("", "-wal"):
        path = Path(settings.db_path + suffix)
        if path.exists():
            assert key.encode("utf-8") not in path.read_bytes()


def test_authenticate(repo):
    tenant, key = create_tenant(repo, "Acme")
    assert authenticate(repo, key) == tenant
    assert authenticate(repo, key + "x") is None
    assert authenticate(repo, "") is None
    assert authenticate(repo, None) is None
    assert authenticate(repo, "k" * 1000) is None


def test_extract_api_key_from_either_header():
    assert extract_api_key("abc", None) == "abc"
    assert extract_api_key("  abc  ", "Bearer zzz") == "abc"
    assert extract_api_key(None, "Bearer zzz") == "zzz"
    assert extract_api_key(None, "bearer zzz") == "zzz"
    assert extract_api_key(None, "Basic zzz") is None
    assert extract_api_key(None, "Bearer") is None
    assert extract_api_key(None, None) is None
    assert extract_api_key("   ", None) is None


def test_duplicate_and_invalid_tenant_names(repo):
    create_tenant(repo, "Acme")
    with pytest.raises(DuplicateTenantError):
        create_tenant(repo, "Acme")
    assert validate_tenant_name("  Acme Ltd  ") == "Acme Ltd"
    for bad in ("", "   ", "a" * 65, "bad/name", "-leading"):
        with pytest.raises(ValueError):
            validate_tenant_name(bad)


def test_list_tenants(repo):
    create_tenant(repo, "Alpha")
    create_tenant(repo, "Beta")
    assert {t.name for t in repo.list_tenants()} == {"Alpha", "Beta"}


def test_tenant_a_cannot_see_or_change_tenant_b_rows(repo):
    a, _ = create_tenant(repo, "A")
    b, _ = create_tenant(repo, "B")
    graph = repo.create_graph(a.id, "city", SMALL_GRAPH, 2, 1)
    job, _ = repo.create_job(a.id, {"n": 1}, "hash", None)

    assert repo.get_graph(a.id, graph.id) is not None
    assert repo.get_graph_data(a.id, graph.id) == SMALL_GRAPH
    assert repo.get_job(a.id, job.id) is not None

    assert repo.get_graph(b.id, graph.id) is None
    assert repo.get_graph_data(b.id, graph.id) is None
    assert repo.get_job(b.id, job.id) is None
    assert repo.mark_running(b.id, job.id) is None
    repo.complete_job(b.id, job.id, {"r": 1})
    repo.fail_job(b.id, job.id, "x")

    untouched = repo.get_job(a.id, job.id)
    assert untouched is not None
    assert untouched.status == "queued"
    assert untouched.result is None
    assert untouched.error is None


def test_idempotency_key_returns_the_same_job_and_is_scoped_per_tenant(repo):
    a, _ = create_tenant(repo, "A")
    b, _ = create_tenant(repo, "B")
    first, created_first = repo.create_job(a.id, {"n": 1}, "hash", "key-1")
    second, created_second = repo.create_job(a.id, {"n": 1}, "hash", "key-1")
    assert created_first is True
    assert created_second is False
    assert first.id == second.id

    other_tenant, created_other = repo.create_job(b.id, {"n": 1}, "hash", "key-1")
    assert created_other is True
    assert other_tenant.id != first.id

    no_key_1, _ = repo.create_job(a.id, {"n": 1}, "hash", None)
    no_key_2, _ = repo.create_job(a.id, {"n": 1}, "hash", None)
    assert no_key_1.id != no_key_2.id


def test_job_state_transitions(repo):
    tenant, _ = create_tenant(repo, "A")
    job, _ = repo.create_job(tenant.id, {"n": 1}, "h", None)
    assert job.status == "queued"

    running = repo.mark_running(tenant.id, job.id)
    assert running is not None
    assert running.status == "running"
    assert repo.mark_running(tenant.id, job.id) is None  # cannot be claimed twice

    repo.complete_job(tenant.id, job.id, {"total": 1.5})
    done = repo.get_job(tenant.id, job.id)
    assert done is not None
    assert done.status == "succeeded"
    assert done.result == {"total": 1.5}

    other, _ = repo.create_job(tenant.id, {"n": 2}, "h2", None)
    repo.fail_job(tenant.id, other.id, "boom")
    failed = repo.get_job(tenant.id, other.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "boom"


def test_requeue_incomplete_resets_running_jobs(repo):
    tenant, _ = create_tenant(repo, "A")
    queued, _ = repo.create_job(tenant.id, {"n": 1}, "h1", None)
    crashed, _ = repo.create_job(tenant.id, {"n": 2}, "h2", None)
    finished, _ = repo.create_job(tenant.id, {"n": 3}, "h3", None)
    repo.mark_running(tenant.id, crashed.id)
    repo.mark_running(tenant.id, finished.id)
    repo.complete_job(tenant.id, finished.id, {"ok": True})

    pending = repo.requeue_incomplete()

    assert sorted(pending) == sorted([(tenant.id, queued.id), (tenant.id, crashed.id)])
    recovered = repo.get_job(tenant.id, crashed.id)
    assert recovered is not None
    assert recovered.status == "queued"
