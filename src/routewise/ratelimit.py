"""Per-tenant rate limiting.

Actual Limitation - buckets live in this process's memory, so with several workers or
machines each one enforces its own limit. A shared store such as Redis behind
the same RateLimiter interface fixes that. Buckets also reset on restart.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    remaining: int
    retry_after: float  # seconds until one request is available; 0.0 when allowed
    limit: int


class RateLimiter(ABC):
    @abstractmethod
    def check(self, key: str) -> RateDecision:
        """Consume one request for key if allowed and report the decision."""
        raise NotImplementedError


class TokenBucketLimiter(RateLimiter):
    """In-memory token bucket: capacity `burst`, refilled at `rate_per_minute`. O(1) per check."""

    def __init__(
        self,
        rate_per_minute: int,
        burst: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate_per_minute < 1:
            raise ValueError("rate_per_minute must be at least 1")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self._rate = rate_per_minute / 60.0
        self._burst = float(burst)
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_seen)
        self._lock = threading.Lock()

    def check(self, key: str) -> RateDecision:
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (self._burst, now))
            tokens = min(self._burst, tokens + max(0.0, now - last) * self._rate)
            if tokens >= 1.0:
                tokens -= 1.0
                self._buckets[key] = (tokens, now)
                return RateDecision(True, int(tokens), 0.0, int(self._burst))
            self._buckets[key] = (tokens, now)
            retry_after = (1.0 - tokens) / self._rate
            return RateDecision(False, 0, retry_after, int(self._burst))
