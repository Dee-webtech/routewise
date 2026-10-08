import pytest

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
