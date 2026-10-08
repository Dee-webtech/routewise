"""Settings from ROUTEWISE_* environment variables, read when the app is built."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    db_path: str = "routewise.db"
    sync_max_stops: int = 50  # more stops than this become a background job (HTTP 202)
    max_stops: int = 2000
    max_vehicles: int = 50
    worker_threads: int = 2
    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 20
    max_graph_nodes: int = 200_000
    log_level: str = "INFO"


def load_settings() -> Settings:
    return Settings(
        db_path=os.environ.get("ROUTEWISE_DB_PATH", "routewise.db"),
        sync_max_stops=_int("ROUTEWISE_SYNC_MAX_STOPS", 50),
        max_stops=_int("ROUTEWISE_MAX_STOPS", 2000),
        max_vehicles=_int("ROUTEWISE_MAX_VEHICLES", 50),
        worker_threads=_int("ROUTEWISE_WORKER_THREADS", 2),
        rate_limit_per_minute=_int("ROUTEWISE_RATE_LIMIT_PER_MINUTE", 120),
        rate_limit_burst=_int("ROUTEWISE_RATE_LIMIT_BURST", 20),
        max_graph_nodes=_int("ROUTEWISE_MAX_GRAPH_NODES", 200_000),
        log_level=os.environ.get("ROUTEWISE_LOG_LEVEL", "INFO").upper(),
    )
