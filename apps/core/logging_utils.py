"""Small helpers for structured user-flow tracking logs."""

from __future__ import annotations

from contextvars import ContextVar
import logging
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
    tracking_logger.info(
        " ".join(f"{key}={_format_value(value)}" for key, value in payload.items())
    )


def log_user_error(user_id: int | None, event: str, **fields: Any) -> None:
    """Write one compact key=value error line for failed user-flow steps."""
    payload = _build_payload(user_id, event, fields)
    tracking_logger.error(
        " ".join(f"{key}={_format_value(value)}" for key, value in payload.items())
    )


def log_user_warning(user_id: int | None, event: str, **fields: Any) -> None:
    """Write one compact key=value warning line for controlled stress issues."""
    payload = _build_payload(user_id, event, fields)
    tracking_logger.warning(
        " ".join(f"{key}={_format_value(value)}" for key, value in payload.items())
    )
