"""Request checks for the endpoints a browser can reach directly.

The AI service has no login of its own. These checks limit what an outsider
can do with it: other websites cannot make changes through a visitor's
browser, uploads are size-capped before they are read, and the costly
endpoints are rate-limited per client.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from starlette.requests import Request
from starlette.responses import JSONResponse

DEFAULT_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
}
# Eight 15 MB photos, a 30 MB recording, and room for the form fields.
MAX_UPLOAD_BYTES = 8 * 15 * 1024 * 1024 + 30 * 1024 * 1024 + 1024 * 1024
# group -> (requests allowed, per this many seconds), counted per client address.
RATE_LIMITS = {"talk": (30, 60), "studio": (30, 3600)}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_hits: dict[tuple[str, str], deque] = defaultdict(deque)


def allowed_origins() -> set[str]:
    """Origins that may call the service from a browser; extend with ALLOWED_ORIGINS."""
    extra = {origin.strip().rstrip("/") for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")}
    return DEFAULT_ORIGINS | {origin for origin in extra if origin}


def reset_rate_limits() -> None:
    _hits.clear()


def _rate_group(method: str, path: str) -> str | None:
    if method == "POST" and path == "/talk":
        return "talk"
    if method in UNSAFE_METHODS and path.startswith("/mentors"):
        return "studio"
    return None


def _retry_after(group: str, client: str) -> int:
    """Record a request; return seconds to wait when the client is over its limit, else 0."""
    limit, window = RATE_LIMITS[group]
    now = time.monotonic()
    hits = _hits[(group, client)]
    while hits and now - hits[0] >= window:
        hits.popleft()
    if len(hits) >= limit:
        return max(1, int(window - (now - hits[0])) + 1)
    hits.append(now)
    return 0


async def guard(request: Request, call_next):
    method, path = request.method, request.url.path

    if method in UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in allowed_origins():
            return JSONResponse({"detail": "This site may not call the mentor service."}, 403)

        if path.startswith("/mentors"):
            length = request.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > MAX_UPLOAD_BYTES):
                return JSONResponse({"detail": "The upload is too large."}, 413)

    group = _rate_group(method, path)
    if group:
        client = request.client.host if request.client else "unknown"
        wait = _retry_after(group, client)
        if wait:
            return JSONResponse(
                {"detail": "Too many requests. Please wait and try again."},
                429,
                headers={"Retry-After": str(wait)},
            )
    return await call_next(request)
