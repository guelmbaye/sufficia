"""HTTP hardening: API key for mutating requests, per-IP rate limit, body size cap, security headers,
request ids and structured access logs. None of this is a safety control for authority — the engine
and the EvidenceGate contract are — but it keeps the service boring to operate."""

from __future__ import annotations

import hmac
import json
import logging
import sys
import threading
import time
import uuid
from collections import deque
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .responses import ExactJSONResponse

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
log = logging.getLogger("sufficia.http")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status", "duration_ms", "client"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", as_json: bool = True) -> None:
    root = logging.getLogger("sufficia")
    if getattr(root, "_sufficia_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if as_json else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False
    root._sufficia_configured = True  # type: ignore[attr-defined]


def _error(status: int, code: str, message: str, request_id: str) -> Response:
    return ExactJSONResponse(
        {"error": {"code": code, "message": message, "detail": {}}}, status_code=status, headers={"X-Request-ID": request_id}
    )


class RateLimiter:
    """Sliding-window limiter per client key (in-process; one API instance for the hackathon)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, *, api_key: str | None, rate_per_minute: int, max_bytes: int):
        super().__init__(app)
        self.api_key = api_key
        self.limiter = RateLimiter(rate_per_minute)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        if len(request_id) > 64 or not request_id.replace("-", "").isalnum():
            request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        client = request.client.host if request.client else "unknown"
        path = request.url.path

        if request.method in MUTATING and not path.startswith("/_dev/"):
            length = request.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > self.max_bytes):
                return _error(413, "PAYLOAD_TOO_LARGE", f"request bodies are limited to {self.max_bytes} bytes", request_id)
            if self.api_key:
                given = request.headers.get("x-api-key", "")
                if not hmac.compare_digest(given.encode(), self.api_key.encode()):
                    return _error(401, "API_KEY_REQUIRED", "a valid X-API-Key header is required for this request", request_id)
            if not self.limiter.allow(client):
                return _error(429, "RATE_LIMITED", "too many requests; retry in a minute", request_id)

        try:
            response = await call_next(request)
        except Exception:  # pragma: no cover - handled by exception handlers normally
            log.exception("unhandled error", extra={"request_id": request_id, "path": path})
            response = _error(500, "INTERNAL_ERROR", "internal error", request_id)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        duration = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "request",
            extra={"request_id": request_id, "method": request.method, "path": path, "status": response.status_code,
                   "duration_ms": duration, "client": client},
        )
        return response
