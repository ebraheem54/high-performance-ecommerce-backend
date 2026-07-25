"""
Requirement 7 — Redis Distributed Lock / Cache Stampede test.

Run BEFORE:
  docker compose run --rm -p 8092:8089 \
    -e LOCUST_MODE=req7_before \
    locust -f locust_tests/req7/locust_req7_distributed_lock.py \
    --host http://nginx:80 -u 20 -r 20 --run-time 15s

Run AFTER:
  docker compose run --rm -p 8092:8089 \
    -e LOCUST_MODE=req7_after \
    locust -f locust_tests/req7/locust_req7_distributed_lock.py \
    --host http://nginx:80 -u 20 -r 20 --run-time 15s

Expected:
  BEFORE: many requests rebuild top_selling_products from DB.
  AFTER : one request rebuilds while the rest wait and read Redis.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any

import gevent
from gevent.event import Event
import requests as setup_requests
from locust import HttpUser, between, events, task


LOCUST_MODE = os.getenv("LOCUST_MODE", "req7_before").strip().lower()
if LOCUST_MODE not in {"req7_before", "req7_after"}:
    LOCUST_MODE = "req7_before"

REQ7_API_MODE = "before" if LOCUST_MODE == "req7_before" else "after"

LOCUST_PASSWORD = os.getenv("LOCUST_PASSWORD", "LocustPass123!")
LOCUST_EMAIL_TPL = os.getenv("LOCUST_EMAIL_TPL", "locust_{i}@test.com")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@demo.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

REQ7_REDIS_HOST = os.getenv("REQ7_REDIS_HOST", "redis")
REQ7_REDIS_PORT = int(os.getenv("REQ7_REDIS_PORT", "6379"))
TOP_SELLING_CACHE_KEY = os.getenv("REQ7_TOP_SELLING_CACHE_KEY", "top_selling_products")
TOP_SELLING_LOCK_KEY = os.getenv("REQ7_TOP_SELLING_LOCK_KEY", "req7:lock:top_selling_products")
PRODUCT_LIST_CACHE_KEY = os.getenv("REQ7_PRODUCT_LIST_CACHE_KEY", "product_list")
PRODUCT_LIST_LOCK_KEY = os.getenv("REQ7_PRODUCT_LIST_LOCK_KEY", "req7:lock:product_list")
REQ7_DELAY_MS = int(os.getenv("REQ7_DELAY_MS", "200"))
REQ7_WAVE_MAX_WAIT_SECONDS = float(os.getenv("REQ7_WAVE_MAX_WAIT_SECONDS", "10"))

_lock = threading.Lock()
SHARED_TOKEN: str | None = None
SETUP_ERROR: str | None = None
TARGET_USERS = 1
READY_USERS = 0
TOP_SELLING_DONE_USERS = 0
WAVE_EVENT = Event()
PRODUCT_LIST_EVENT = Event()

def _empty_counts() -> dict[str, int]:
    return {
        "requests": 0,
        "failures": 0,
        "cache_hit": 0,
        "lock_acquired": 0,
        "db_query_executed": 0,
        "served_after_wait": 0,
        "fallback_used": 0,
    }


ENDPOINT_COUNTS = {
    "top_selling": _empty_counts(),
    "product_list": _empty_counts(),
}
WAITED_MS = {
    "top_selling": [],
    "product_list": [],
}
CACHE_STATUS_COUNTS = {
    "top_selling": {},
    "product_list": {},
}


def _banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def _print_metric_table(title: str, rows: list[tuple[str, object]]) -> None:
    metric_width = 26
    value_width = 12
    border = "+" + "-" * (metric_width + 2) + "+" + "-" * (value_width + 2) + "+"

    print("\n" + title)
    print(border)
    print(f"| {'Metric':<{metric_width}} | {'Value':>{value_width}} |")
    print(border)
    for metric, value in rows:
        print(f"| {metric:<{metric_width}} | {str(value):>{value_width}} |")
    print(border)


def _print_before_table(title: str, stats, counts: dict[str, int], statuses: dict[str, int], avg_wait: float) -> None:
    rows = [
        ("Total Requests", counts["requests"]),
        ("Failures", counts["failures"]),
        ("Cache HIT", statuses.get("HIT", 0)),
        ("DB Rebuilds", counts["db_query_executed"]),
        ("Fallback DB Reads", counts["fallback_used"]),
        ("Avg Wait Time (ms)", f"{avg_wait:.0f}"),
    ]
    _print_metric_table(title, rows)


def _print_after_table(title: str, stats, counts: dict[str, int], statuses: dict[str, int], avg_wait: float, protected: bool) -> None:
    rows = [
        ("Total Requests", counts["requests"]),
        ("Failures", counts["failures"]),
        ("Cache HIT", statuses.get("HIT", 0)),
        ("DB Rebuilds", counts["db_query_executed"]),
        ("Served After Wait", counts["served_after_wait"]),
        ("Fallback DB Reads", counts["fallback_used"]),
        ("Avg Wait Time (ms)", f"{avg_wait:.0f}"),
        ("Protected", "YES" if protected else "NO"),
    ]
    _print_metric_table(title, rows)


def _redis_command(*parts: str) -> bytes | None:
    try:
        encoded_parts = [str(part).encode("utf-8") for part in parts]
        command = b"*" + str(len(encoded_parts)).encode("utf-8") + b"\r\n"
        for part in encoded_parts:
            command += b"$" + str(len(part)).encode("utf-8") + b"\r\n"
            command += part + b"\r\n"

        with socket.create_connection((REQ7_REDIS_HOST, REQ7_REDIS_PORT), timeout=1.0) as sock:
            sock.sendall(command)
            return sock.recv(1024)
    except Exception as exc:
        print(f"  [WARN] Redis command failed: {exc}")
        return None


def _delete_cache_keys(*keys: str) -> None:
    expanded: list[str] = []
    for key in keys:
        expanded.append(key)
        expanded.append(f":1:{key}")
    if expanded:
        _redis_command("DEL", *expanded)


def _login_once(base: str) -> str | None:
    if ADMIN_TOKEN:
        return ADMIN_TOKEN

    for email, password in [
        (LOCUST_EMAIL_TPL.format(i=1), LOCUST_PASSWORD),
        (ADMIN_EMAIL, ADMIN_PASSWORD),
    ]:
        try:
            response = setup_requests.post(
                f"{base}/api/users/login/",
                json={"email": email, "password": password},
                timeout=15,
            )
            if response.status_code == 200:
                return response.json().get("token")
        except Exception:
            continue
    return None


def _auth_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Token {token}"} if token else {}


def _record(endpoint: str, metadata: dict[str, Any] | None = None, *, failed: bool = False) -> None:
    with _lock:
        counts = ENDPOINT_COUNTS[endpoint]
        counts["requests"] += 1
        if failed:
            counts["failures"] += 1
            return
        if not metadata:
            return

        for key in (
            "cache_hit",
            "lock_acquired",
            "db_query_executed",
            "served_after_wait",
            "fallback_used",
        ):
            if metadata.get(key):
                counts[key] += 1

        waited_ms = metadata.get("waited_ms")
        if isinstance(waited_ms, int):
            WAITED_MS[endpoint].append(waited_ms)

        cache_status = metadata.get("cache_status")
        if isinstance(cache_status, str) and cache_status:
            statuses = CACHE_STATUS_COUNTS[endpoint]
            statuses[cache_status] = statuses.get(cache_status, 0) + 1


def _metadata_from_headers(headers: Any) -> dict[str, Any]:
    cache_status = headers.get("X-Req7-Cache-Status", "UNKNOWN")
    try:
        waited_ms = int(headers.get("X-Req7-Waited-Ms", "0"))
    except (TypeError, ValueError):
        waited_ms = 0

    return {
        "cache_status": cache_status,
        "cache_hit": cache_status == "HIT",
        "db_query_executed": headers.get("X-Req7-DB-Query") == "1",
        "lock_acquired": headers.get("X-Req7-Lock-Acquired") == "1",
        "served_after_wait": headers.get("X-Req7-Served-After-Wait") == "1",
        "fallback_used": headers.get("X-Req7-Fallback") == "1",
        "waited_ms": waited_ms,
    }


def _mark_top_selling_done() -> None:
    global TOP_SELLING_DONE_USERS
    with _lock:
        TOP_SELLING_DONE_USERS += 1
        if TOP_SELLING_DONE_USERS >= TARGET_USERS:
            PRODUCT_LIST_EVENT.set()


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    global SHARED_TOKEN, SETUP_ERROR, TARGET_USERS, READY_USERS, TOP_SELLING_DONE_USERS, WAVE_EVENT, PRODUCT_LIST_EVENT

    base = environment.host.rstrip("/")
    SHARED_TOKEN = _login_once(base)
    if not SHARED_TOKEN:
        SETUP_ERROR = "authentication_failed"

    TARGET_USERS = int(getattr(environment.parsed_options, "num_users", None) or 1)
    READY_USERS = 0
    TOP_SELLING_DONE_USERS = 0
    WAVE_EVENT = Event()
    PRODUCT_LIST_EVENT = Event()
    for key in ENDPOINT_COUNTS:
        ENDPOINT_COUNTS[key] = _empty_counts()
        WAITED_MS[key] = []
        CACHE_STATUS_COUNTS[key] = {}

    _delete_cache_keys(
        TOP_SELLING_CACHE_KEY,
        TOP_SELLING_LOCK_KEY,
        PRODUCT_LIST_CACHE_KEY,
        PRODUCT_LIST_LOCK_KEY,
    )

    _banner(f"REQ7 START — {LOCUST_MODE.upper()}")
    print(
        f"mode={REQ7_API_MODE} users={TARGET_USERS} delay_ms={REQ7_DELAY_MS} "
        f"top_selling_lock={TOP_SELLING_LOCK_KEY} product_list_lock={PRODUCT_LIST_LOCK_KEY}"
    )


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    if SETUP_ERROR:
        print(f"setup_error={SETUP_ERROR}")
    for endpoint, title in [
        ("top_selling", "/api/products/top-selling/"),
        ("product_list", "/api/products/"),
    ]:
        counts = ENDPOINT_COUNTS[endpoint]
        statuses = CACHE_STATUS_COUNTS[endpoint]
        waits = WAITED_MS[endpoint]
        avg_wait = sum(waits) / len(waits) if waits else 0
        protected = (
            LOCUST_MODE == "req7_after"
            and counts["lock_acquired"] >= 1
            and counts["db_query_executed"] == 1
            and counts["fallback_used"] == 0
        )

        if LOCUST_MODE == "req7_before":
            _print_before_table(f"REQ7 BEFORE RESULT — {title}", None, counts, statuses, avg_wait)
        else:
            _print_after_table(f"REQ7 AFTER RESULT — {title}", None, counts, statuses, avg_wait, protected)


class Req7DistributedLockUser(HttpUser):
    wait_time = between(10, 15)
    weight = 1

    def on_start(self):
        global READY_USERS
        self.token = SHARED_TOKEN
        self.sent_req7_request = False
        with _lock:
            READY_USERS += 1
            if READY_USERS >= TARGET_USERS:
                WAVE_EVENT.set()

    @task
    def top_selling_stampede(self):
        if self.sent_req7_request:
            time.sleep(1)
            return
        self.sent_req7_request = True

        if not self.token:
            _record("top_selling", failed=True)
            _record("product_list", failed=True)
            return

        if not WAVE_EVENT.wait(timeout=REQ7_WAVE_MAX_WAIT_SECONDS):
            WAVE_EVENT.set()
        gevent.sleep(0)

        try:
            self._hit_req7_endpoint(
                endpoint="top_selling",
                path=f"/api/products/top-selling/?req7_mode={REQ7_API_MODE}",
                name="/api/products/top-selling/",
            )
        finally:
            _mark_top_selling_done()

        if not PRODUCT_LIST_EVENT.wait(timeout=REQ7_WAVE_MAX_WAIT_SECONDS):
            PRODUCT_LIST_EVENT.set()
        gevent.sleep(0)

        self._hit_req7_endpoint(
            endpoint="product_list",
            path=f"/api/products/?req7_mode={REQ7_API_MODE}",
            name="/api/products/",
        )

    def _hit_req7_endpoint(self, endpoint: str, path: str, name: str) -> None:
        if REQ7_DELAY_MS > 0:
            path += f"&req7_delay_ms={REQ7_DELAY_MS}"

        with self.client.get(
            path,
            headers=_auth_headers(self.token),
            name=name,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                _record(endpoint, failed=True)
                response.failure(f"{response.status_code} {response.text[:160]}")
                return

            _record(endpoint, _metadata_from_headers(response.headers))
            response.success()
