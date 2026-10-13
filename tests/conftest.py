import pytest
from fastapi.testclient import TestClient

from routewise.api import create_app
from routewise.auth import create_tenant
from routewise.config import Settings
from routewise.db import Repository


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        db_path=str(tmp_path / "test.db"),
        sync_max_stops=50,
        worker_threads=2,
        rate_limit_per_minute=600,
        rate_limit_burst=100,
    )


@pytest.fixture()
def repo(settings):
    repository = Repository(settings.db_path)
    repository.init_schema()
    return repository


@pytest.fixture()
def app(settings):
    return create_app(settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def _account(repo, name):
    tenant, key = create_tenant(repo, name)
    return {"id": tenant.id, "key": key, "headers": {"X-API-Key": key}}


@pytest.fixture()
def tenant_a(repo):
    return _account(repo, "Tenant A")


@pytest.fixture()
def tenant_b(repo):
    return _account(repo, "Tenant B")
