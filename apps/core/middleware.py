"""Request tracing middleware for Requirement 10 logs."""

from __future__ import annotations

import time
import uuid

from apps.core.logging_utils import (
    clear_request_context,
    log_user_error,
    log_user_event,
    log_user_warning,
    set_request_context,
)

SLOW_REQUEST_WARNING_MS = 2_000


class RequestTrackingMiddleware:
    """Attach request metadata to logs and record one access event per request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:12]
        started = time.perf_counter()
        set_request_context(request_id, request.method, request.path)

        try:
            response = self.get_response(request)
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            user_id = self._user_id(request)
            log_user_error(
                user_id,
                "request.exception",
                status_code=500,
                elapsed_ms=elapsed_ms,
                result="failed",
                reason=exc.__class__.__name__,
            )
            clear_request_context()
            raise

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        user_id = self._user_id(request)
        status_code = response.status_code
        response["X-Request-ID"] = request_id

        payload = {
            "status_code": status_code,
            "elapsed_ms": elapsed_ms,
            "result": "completed",
        }
        if status_code >= 500:
            log_user_error(user_id, "request.completed", **payload)
        elif status_code in {400, 409, 429} or elapsed_ms > SLOW_REQUEST_WARNING_MS:
            log_user_warning(user_id, "request.completed", **payload)
        else:
            log_user_event(user_id, "request.completed", **payload)

        clear_request_context()
        return response

    @staticmethod
    def _user_id(request) -> int | None:
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            return user.id
        return None
