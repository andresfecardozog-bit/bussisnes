"""Rate limiting simple en memoria (por proceso)."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request

from app.config import RATE_LIMIT_HEAVY_PER_10MIN, RATE_LIMIT_LOGIN_PER_MINUTE


@dataclass(frozen=True)
class RateRule:
    bucket: str
    max_requests: int
    window_seconds: int


class _MemoryLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.time()
        floor = now - window_seconds
        with self._lock:
            q = self._events[key]
            while q and q[0] < floor:
                q.popleft()
            if len(q) >= max_requests:
                return False
            q.append(now)
            return True


_limiter = _MemoryLimiter()


def _rule_for(request: Request) -> RateRule | None:
    path = request.url.path
    method = request.method.upper()
    if method == "POST" and path == "/auth/login":
        return RateRule("auth_login", RATE_LIMIT_LOGIN_PER_MINUTE, 60)
    if method == "POST" and path in {"/profiles/draft", "/profiles/refine"}:
        return RateRule("heavy_profiles", RATE_LIMIT_HEAVY_PER_10MIN, 600)
    if method == "POST" and path.endswith("/refine"):
        return RateRule("heavy_profiles", RATE_LIMIT_HEAVY_PER_10MIN, 600)
    if method == "POST" and path.endswith("/generate"):
        return RateRule("heavy_profiles", RATE_LIMIT_HEAVY_PER_10MIN, 600)
    return None


def is_request_allowed(request: Request) -> bool:
    rule = _rule_for(request)
    if rule is None:
        return True
    ip = request.client.host if request.client else "unknown"
    key = f"{rule.bucket}:{ip}"
    return _limiter.allow(key, rule.max_requests, rule.window_seconds)
