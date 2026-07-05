"""
A deliberately small, dependency-free rate limiter.

Keyed by client IP + route path, sliding 60-second window, in-memory. This
is enough to stop a runaway client or a stray bot from burning your Gemini
quota or hammering Firebase Admin — it is NOT a substitute for a real
distributed limiter if you scale this service to multiple instances later
(at that point, move the counters to Redis and keep the same interface).

Deliberately not using a third-party rate-limit library here: this whole
job is ~30 lines, and one fewer dependency is one fewer thing that can
break across FastAPI/Starlette version bumps.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limits: Dict[str, int], window_seconds: int = 60):
        """
        `limits` maps an exact request path (e.g. "/v1/feedback/generate")
        to a max-requests-per-window integer. Paths not listed are not
        rate limited by this middleware.
        """
        super().__init__(app)
        self._limits = limits
        self._window = window_seconds
        self._hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable):
        limit = self._limits.get(request.url.path)
        if limit is not None:
            client_ip = self._client_ip(request)
            bucket_key = (client_ip, request.url.path)
            now = time.monotonic()
            bucket = self._hits[bucket_key]

            while bucket and now - bucket[0] > self._window:
                bucket.popleft()

            if len(bucket) >= limit:
                retry_after = max(1, int(self._window - (now - bucket[0])))
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": {
                            "code": "rate_limited",
                            "message": f"Too many requests. Try again in {retry_after}s.",
                        }
                    },
                    headers={"Retry-After": str(retry_after)},
                )

            bucket.append(now)

        return await call_next(request)

    @staticmethod
    def _client_ip(request: Request) -> str:
        # Render (and most PaaS providers) sit behind a proxy that sets
        # X-Forwarded-For; fall back to the raw socket address locally.
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
