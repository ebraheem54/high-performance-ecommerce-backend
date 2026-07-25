"""
Prometheus metrics helpers for app-level monitoring.
"""

import os
import time

from django.http import HttpRequest
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from prometheus_client import CollectorRegistry, multiprocess

REQUEST_COUNT = Counter(
    "django_http_requests_total",
    "Total HTTP requests handled by the Django app.",
    ["method", "view", "status_code"],
)

REQUEST_LATENCY = Histogram(
    "django_http_request_duration_seconds",
    "HTTP request latency in seconds for the Django app.",
    ["method", "view"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

CART_ACTIONS = Counter(
    "ecommerce_cart_actions_total",
    "Cart actions by operation and outcome.",
    ["action", "outcome"],
)

ORDER_EVENTS = Counter(
    "ecommerce_order_events_total",
    "Order and payment events by type and outcome.",
    ["event", "outcome"],
)


def now() -> float:
    return time.perf_counter()


def _ensure_multiproc_dir() -> str | None:
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if not multiproc_dir:
        return None
    try:
        os.makedirs(multiproc_dir, exist_ok=True)
    except OSError:
        return None
    return multiproc_dir


def view_label(request: HttpRequest) -> str:
    match = getattr(request, "resolver_match", None)
    if match and match.route:
        return match.route
    return request.path


def observe_request(request: HttpRequest, response_status: int, started_at: float) -> None:
    method = request.method
    view = view_label(request)
    status_code = str(response_status)
    try:
        _ensure_multiproc_dir()
        REQUEST_COUNT.labels(method=method, view=view, status_code=status_code).inc()
        REQUEST_LATENCY.labels(method=method, view=view).observe(
            max(time.perf_counter() - started_at, 0)
        )
    except (FileNotFoundError, OSError, ValueError):
        # Metrics must never break live requests.
        return


def record_cart_action(action: str, outcome: str) -> None:
    try:
        _ensure_multiproc_dir()
        CART_ACTIONS.labels(action=action, outcome=outcome).inc()
    except (FileNotFoundError, OSError, ValueError):
        return


def record_order_event(event: str, outcome: str) -> None:
    try:
        _ensure_multiproc_dir()
        ORDER_EVENTS.labels(event=event, outcome=outcome).inc()
    except (FileNotFoundError, OSError, ValueError):
        return


def render_metrics() -> tuple[bytes, str]:
    multiproc_dir = _ensure_multiproc_dir()
    if multiproc_dir:
        registry = CollectorRegistry()
        try:
            multiprocess.MultiProcessCollector(registry)
            return generate_latest(registry), CONTENT_TYPE_LATEST
        except (FileNotFoundError, OSError, ValueError):
            return b"", CONTENT_TYPE_LATEST
    try:
        return generate_latest(), CONTENT_TYPE_LATEST
    except (FileNotFoundError, OSError, ValueError):
        return b"", CONTENT_TYPE_LATEST
