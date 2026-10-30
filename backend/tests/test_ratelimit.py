from fastapi.testclient import TestClient

from app import ratelimit
from app.main import app
from app.ratelimit import RateLimiter

client = TestClient(app)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_allows_up_to_the_limit_then_blocks():
    clock = Clock()
    rl = RateLimiter(limit=3, window_s=10, block_s=60, clock=clock)
    assert [rl.check("1.1.1.1") for _ in range(3)] == [0.0, 0.0, 0.0]
    assert rl.check("1.1.1.1") == 60
    assert rl.is_blocked("1.1.1.1")
    clock.t += 30
    assert 0 < rl.check("1.1.1.1") <= 30  # still blocked, wait shrinks
    clock.t += 31
    assert rl.check("1.1.1.1") == 0.0  # block over, counting starts afresh


def test_window_slides_and_clients_are_independent():
    clock = Clock()
    rl = RateLimiter(limit=2, window_s=10, block_s=60, clock=clock)
    rl.check("a"); rl.check("a")
    assert rl.check("b") == 0.0  # another address is not affected
    clock.t += 11  # a's hits fall out of the window
    assert rl.check("a") == 0.0


def test_prune_forgets_idle_clients():
    clock = Clock()
    rl = RateLimiter(limit=2, window_s=10, block_s=5, clock=clock)
    for _ in range(3):
        rl.check("gone")
    assert "gone" in rl._blocked
    clock.t += 20
    rl.check("other")
    assert "gone" not in rl._blocked and "gone" not in rl._hits


def test_api_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setattr(ratelimit, "limiter", RateLimiter(limit=2, window_s=10, block_s=60))
    assert client.get("/api/samples").status_code == 200
    assert client.get("/api/samples").status_code == 200
    res = client.get("/api/samples")
    assert res.status_code == 429
    assert res.headers["Retry-After"] == "60"
    assert "too many requests" in res.json()["detail"]
    assert client.get("/health").status_code == 200  # not under /api/, never limited


def test_forwarded_header_only_trusted_behind_proxy(monkeypatch):
    rl = RateLimiter(limit=1, window_s=10, block_s=60)
    monkeypatch.setattr(ratelimit, "limiter", rl)
    headers = {"X-Forwarded-For": "9.9.9.9"}
    client.get("/api/samples", headers=headers)
    assert client.get("/api/samples", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 429  # header ignored
    monkeypatch.setattr(ratelimit, "TRUST_PROXY", True)
    monkeypatch.setattr(ratelimit, "limiter", RateLimiter(limit=1, window_s=10, block_s=60))
    client.get("/api/samples", headers=headers)
    assert client.get("/api/samples", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200
    assert client.get("/api/samples", headers=headers).status_code == 429
