"""Small helpers for structured user-flow tracking logs."""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
from inspect import signature
import logging
import time
from typing import Any

tracking_logger = logging.getLogger("user_tracking")

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_request_method: ContextVar[str | None] = ContextVar("request_method", default=None)
_request_endpoint: ContextVar[str | None] = ContextVar("request_endpoint", default=None)


class MaxLevelFilter(logging.Filter):
    """Allow records up to and including max_level."""

    def __init__(self, max_level: int | str) -> None:
        super().__init__()
        if isinstance(max_level, str):
            max_level = logging._nameToLevel[max_level]
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.max_level


def set_request_context(request_id: str, method: str, endpoint: str) -> None:
    _request_id.set(request_id)
    _request_method.set(method)
    _request_endpoint.set(endpoint)


def clear_request_context() -> None:
    _request_id.set(None)
    _request_method.set(None)
    _request_endpoint.set(None)


def _context_fields() -> dict[str, str]:
    context = {
        "request_id": _request_id.get(),
        "method": _request_method.get(),
        "endpoint": _request_endpoint.get(),
    }
    return {key: value for key, value in context.items() if value}


def _format_value(value: Any) -> str:
    text = str(value)
    return text.replace("\n", " ").replace("\r", " ")


def _format_fields(fields: dict[str, Any]) -> str:
    return " ".join(
        f"{key}={_format_value(value)}"
        for key, value in fields.items()
        if value is not None
    )


def _build_payload(user_id: int | None, event: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        **_context_fields(),
        "user_id": user_id if user_id is not None else "anonymous",
        "event": event,
        **fields,
    }


def log_user_event(user_id: int | None, event: str, **fields: Any) -> None:
    """Write one compact key=value line for tracing a user's flow."""
    payload = _build_payload(user_id, event, fields)
    tracking_logger.info(_format_fields(payload))


def log_user_error(user_id: int | None, event: str, **fields: Any) -> None:
    """Write one compact key=value error line for failed user-flow steps."""
    payload = _build_payload(user_id, event, fields)
    tracking_logger.error(_format_fields(payload))


def log_user_warning(user_id: int | None, event: str, **fields: Any) -> None:
    """Write one compact key=value warning line for controlled stress issues."""
    payload = _build_payload(user_id, event, fields)
    tracking_logger.warning(
        _format_fields(payload)
    )


def log_service_call(
    event: str,
    *,
    logger_name: str | None = None,
    context_builder=None,
    result_builder=None,
):
    """
    Decorate a service function with consistent lifecycle logging.

    The wrapped function emits one success log and one failure log shape across
    the service layer, while business-specific logs remain inside the function.
    """

    def decorator(func):
        func_logger = logging.getLogger(logger_name or func.__module__)
        func_signature = signature(func)

        @wraps(func)
        def wrapper(*args, **kwargs):
            bound = func_signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            context = context_builder(bound.arguments) if context_builder else {}
            started = time.perf_counter()

            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                failure_fields = {
                    "service_event": event,
                    "stage": "error",
                    "elapsed_ms": elapsed_ms,
                    "error": exc.__class__.__name__,
                    **context,
                }
                log_method = (
                    func_logger.warning if isinstance(exc, ValueError) else func_logger.error
                )
                log_method(_format_fields(failure_fields))
                raise

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            result_fields = result_builder(result, bound.arguments) if result_builder else {}
            success_fields = {
                "service_event": event,
                "stage": "success",
                "elapsed_ms": elapsed_ms,
                **context,
                **result_fields,
            }
            func_logger.info(_format_fields(success_fields))
            return result

        return wrapper

    return decorator
