import pytest

from routewise.ratelimit import TokenBucketLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_burst_then_block_then_refill():
    clock = FakeClock()
    limiter = TokenBucketLimiter(rate_per_minute=60, burst=3, clock=clock)  # 1 token / second
    decisions = [limiter.check("t") for _ in range(3)]
    assert all(d.allowed for d in decisions)
    assert [d.remaining for d in decisions] == [2, 1, 0]

    blocked = limiter.check("t")
    assert blocked.allowed is False
    assert blocked.remaining == 0
    assert blocked.retry_after == pytest.approx(1.0)
    assert blocked.limit == 3

    clock.now += 1.0
    assert limiter.check("t").allowed is True
    assert limiter.check("t").allowed is False


def test_refill_is_capped_at_the_burst_size():
    clock = FakeClock()
    limiter = TokenBucketLimiter(rate_per_minute=60, burst=2, clock=clock)
    assert limiter.check("t").allowed
    clock.now += 3600.0
    assert limiter.check("t").remaining == 1  # capped at 2, minus the one just used
    assert limiter.check("t").allowed
    assert limiter.check("t").allowed is False


def test_keys_are_independent():
    clock = FakeClock()
    limiter = TokenBucketLimiter(rate_per_minute=60, burst=1, clock=clock)
    assert limiter.check("a").allowed
    assert limiter.check("a").allowed is False
    assert limiter.check("b").allowed


def test_invalid_configuration_is_rejected():
    with pytest.raises(ValueError):
        TokenBucketLimiter(rate_per_minute=0, burst=1)
    with pytest.raises(ValueError):
        TokenBucketLimiter(rate_per_minute=10, burst=0)
