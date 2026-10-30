"""Per-IP rate limit with a temporary block for clients that flood the API.

Every /api/ call counts. A client that sends more than RATE_LIMIT_REQUESTS
within RATE_LIMIT_WINDOW_S is blocked for RATE_LIMIT_BLOCK_S and gets 429 with
a Retry-After header. State is in memory (per process) and gone after a
restart; good enough to stop one machine from hammering the LLM endpoints.
The client address is the socket peer; X-Forwarded-For is only trusted when
EHDS_TRUST_PROXY=1, because any client can set that header itself.
"""

import threading
import time
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import (
    RATE_LIMIT_BLOCK_S,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_S,
    TRUST_PROXY,
)


class RateLimiter:
    def __init__(self, limit: int = RATE_LIMIT_REQUESTS, window_s: float = RATE_LIMIT_WINDOW_S,
                 block_s: float = RATE_LIMIT_BLOCK_S, clock=time.monotonic):
        self.limit, self.window_s, self.block_s, self.clock = limit, window_s, block_s, clock
        self._hits: dict[str, deque[float]] = {}
        self._blocked: dict[str, float] = {}  # ip -> moment the block ends
        self._lock = threading.Lock()
        self._last_prune = clock()

    def check(self, ip: str) -> float:
        """Record one hit. Returns 0 when allowed, else the seconds until the client may try again."""
        now = self.clock()
        with self._lock:
            self._prune(now)
            until = self._blocked.get(ip, 0.0)
            if until > now:
                return until - now
            hits = self._hits.setdefault(ip, deque())
            while hits and hits[0] <= now - self.window_s:
                hits.popleft()
            hits.append(now)
            if len(hits) > self.limit:
                self._blocked[ip] = now + self.block_s
                hits.clear()
                return self.block_s
            return 0.0

    def is_blocked(self, ip: str) -> bool:
        return self._blocked.get(ip, 0.0) > self.clock()

    def _prune(self, now: float) -> None:
        """Forget idle clients so the tables do not grow with every address ever seen."""
        if now - self._last_prune < self.window_s:
            return
        self._last_prune = now
        self._blocked = {ip: t for ip, t in self._blocked.items() if t > now}
        self._hits = {ip: h for ip, h in self._hits.items() if h and h[-1] > now - self.window_s}


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    if TRUST_PROXY and (forwarded := request.headers.get("x-forwarded-for")):
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def middleware(request: Request, call_next):
    if request.url.path.startswith("/api/") and (wait := limiter.check(client_ip(request))):
        retry = max(1, int(wait + 0.999))
        return JSONResponse(
            status_code=429,
            content={"detail": f"too many requests from this address; try again in {retry} s"},
            headers={"Retry-After": str(retry)},
        )
    return await call_next(request)
