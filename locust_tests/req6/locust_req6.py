"""
Merged Locust file — Requirement 6 (BEFORE + AFTER)
High-Performance E-Commerce Backend
Parallel Programming Course 2026

How this file was built (per explicit request):
  - AFTER branch  : kept 100% unchanged from the combined req6_before/req6_after file.
  - BEFORE branch : replaced with the exact logic from the standalone "BEFORE only"
                    file (verbose setup banners, per-request timing via _record(),
                    detailed teardown report, clear-cache-before-and-after-each-request).

Cached/read-heavy targets agreed for Requirement 6:
  1. products list
  2. product detail by id
  3. top-selling products
  4. product rating summary

Run BEFORE:
  docker compose run --rm -p 8091:8089 \
    -e LOCUST_MODE=req6_before \
    locust -f locustfile.py --host http://nginx:80

Run AFTER:
  docker compose run --rm -p 8091:8089 \
    -e LOCUST_MODE=req6_after \
    locust -f locustfile.py --host http://nginx:80

UI:
  http://127.0.0.1:8091
"""

from __future__ import annotations

import os
import random
import socket
import threading
import time
from typing import Any

import requests as setup_requests
from locust import HttpUser, between, events, task


# ══════════════════════════════════════════════════════════════════════════════
# MODE SELECTION
# ══════════════════════════════════════════════════════════════════════════════

LOCUST_MODE = os.getenv("LOCUST_MODE", "req6_before").strip().lower()
if LOCUST_MODE not in {"req6_before", "req6_after"}:
    LOCUST_MODE = "req6_before"
IS_BEFORE = LOCUST_MODE == "req6_before"


# ══════════════════════════════════════════════════════════════════════════════
# CREDENTIALS
# ══════════════════════════════════════════════════════════════════════════════

LOCUST_PASSWORD = os.getenv("LOCUST_PASSWORD", "LocustPass123!")
LOCUST_EMAIL_TPL = os.getenv("LOCUST_EMAIL_TPL", "locust_{i}@test.com")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@demo.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()


# ══════════════════════════════════════════════════════════════════════════════
# REQ 6 CACHE SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

REQ6_REDIS_HOST = os.getenv("REQ6_REDIS_HOST", "redis")
REQ6_REDIS_PORT = int(os.getenv("REQ6_REDIS_PORT", "6379"))

PRODUCT_LIST_CACHE_KEY = os.getenv("REQ6_PRODUCT_LIST_CACHE_KEY", "product_list")
PRODUCT_DETAIL_CACHE_PREFIX = os.getenv("REQ6_PRODUCT_DETAIL_CACHE_PREFIX", "product_detail")
TOP_SELLING_CACHE_KEY = os.getenv("REQ6_TOP_SELLING_CACHE_KEY", "top_selling_products")
RATING_SUMMARY_CACHE_PREFIX = os.getenv(
    "REQ6_RATING_SUMMARY_CACHE_PREFIX",
    "product_rating_summary",
)
REQ6_BYPASS_CACHE_HEADER = "X-Req6-Bypass-Cache"

# ══════════════════════════════════════════════════════════════════════════════
# SHARED STATE
# ══════════════════════════════════════════════════════════════════════════════

_lock = threading.Lock()

ALL_PRODUCT_IDS: list[int] = []
SHARED_TOKEN: str | None = None
SETUP_ERROR: str | None = None  # used by the AFTER result table

# BEFORE-only metrics (kept exactly as in the standalone BEFORE file)
req6_product_list_times_ms: list[float] = []
req6_product_detail_times_ms: list[float] = []
req6_top_selling_times_ms: list[float] = []
req6_rating_summary_times_ms: list[float] = []

req6_total_requests = 0
req6_total_failures = 0


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — SHARED
# ══════════════════════════════════════════════════════════════════════════════

def _banner(title: str) -> None:
    print("\n" + "═" * 72)
    print(f"  {title}")
    print("═" * 72)


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0


def _record(metric: str, value: float | None = None, failed: bool = False) -> None:
    """BEFORE-only: records per-request timing/failure into the global metric lists."""
    global req6_total_requests, req6_total_failures

    with _lock:
        req6_total_requests += 1

        if failed:
            req6_total_failures += 1

        if value is None:
            return

        if metric == "product_list":
            req6_product_list_times_ms.append(value)
        elif metric == "product_detail":
            req6_product_detail_times_ms.append(value)
        elif metric == "top_selling":
            req6_top_selling_times_ms.append(value)
        elif metric == "rating_summary":
            req6_rating_summary_times_ms.append(value)


def _redis_command(*parts: str) -> bytes | None:
    """
    Minimal Redis protocol client.

    Why not import redis?
      The official Locust Docker image may not include redis-py.
      Using a tiny socket client keeps this file self-contained.
    """
    try:
        encoded_parts = [str(part).encode("utf-8") for part in parts]

        command = b"*" + str(len(encoded_parts)).encode("utf-8") + b"\r\n"
        for part in encoded_parts:
            command += b"$" + str(len(part)).encode("utf-8") + b"\r\n"
            command += part + b"\r\n"

        with socket.create_connection((REQ6_REDIS_HOST, REQ6_REDIS_PORT), timeout=1.0) as sock:
            sock.sendall(command)
            return sock.recv(1024)

    except Exception as exc:
        print(f"  [WARN] Redis command failed: {exc}")
        return None


def _delete_cache_keys(*keys: str) -> None:
    """
    Delete both raw keys and Django cache-versioned keys.

    django-redis often stores:
      product_list
      :1:product_list
    """
    expanded: list[str] = []
    for key in keys:
        expanded.append(key)
        expanded.append(f":1:{key}")

    if expanded:
        _redis_command("DEL", *expanded)


def _product_detail_cache_keys(product_id: int) -> list[str]:
    return [
        f"{PRODUCT_DETAIL_CACHE_PREFIX}:{product_id}",
        f"{PRODUCT_DETAIL_CACHE_PREFIX}:{product_id}:customer",
        f"{PRODUCT_DETAIL_CACHE_PREFIX}:{product_id}:admin",
    ]


def _rating_summary_cache_key(product_id: int) -> str:
    return f"{RATING_SUMMARY_CACHE_PREFIX}:{product_id}"


def _clear_product_list_cache() -> None:
    _delete_cache_keys(PRODUCT_LIST_CACHE_KEY)


def _clear_product_detail_cache(product_id: int) -> None:
    _delete_cache_keys(*_product_detail_cache_keys(product_id))


def _clear_top_selling_cache() -> None:
    _delete_cache_keys(TOP_SELLING_CACHE_KEY)


def _clear_rating_summary_cache(product_id: int) -> None:
    _delete_cache_keys(_rating_summary_cache_key(product_id))


def _clear_all_req6_cache(product_ids: list[int] | None = None) -> None:
    """AFTER-only: clear everything once at startup before warming the cache."""
    _clear_product_list_cache()
    _clear_top_selling_cache()
    if product_ids:
        for product_id in product_ids:
            _clear_product_detail_cache(product_id)
            _clear_rating_summary_cache(product_id)


def _auth_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Token {token}"} if token else {}

def _req6_headers(token: str | None) -> dict[str, str]:
    headers = _auth_headers(token)
    if IS_BEFORE:
        headers[REQ6_BYPASS_CACHE_HEADER] = "1"
    return headers

def _extract_product_ids(payload: Any) -> list[int]:
    """
    Robustly extract product IDs from different API response shapes:
      - [ {id: 1}, ... ]
      - {results: [...]}
      - {data: [...]}
      - {data: {results: [...]}}
      - {products: [...]}
    """
    ids: list[int] = []

    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                ids.append(item["id"])
        return ids

    if isinstance(payload, dict):
        if isinstance(payload.get("id"), int) and (
            "price" in payload or "stock" in payload or "name" in payload
        ):
            return [payload["id"]]

        for key in ("results", "data", "items", "products", "objects"):
            nested = payload.get(key)
            nested_ids = _extract_product_ids(nested)
            if nested_ids:
                return nested_ids

        for value in payload.values():
            nested_ids = _extract_product_ids(value)
            if nested_ids:
                return nested_ids

    return ids


def _probe_product_ids(base: str, headers: dict[str, str], limit: int = 50) -> list[int]:
    """
    Fallback if the products list response shape is unexpected.
    It probes product detail endpoints and collects real existing IDs.
    """
    found: list[int] = []

    for product_id in range(1, 201):
        try:
            response = setup_requests.get(
                f"{base}/api/products/{product_id}/",
                headers=headers,
                timeout=5,
            )
            if response.status_code == 200:
                found.append(product_id)
                if len(found) >= limit:
                    break
        except Exception:
            continue

    return found


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — AFTER ONLY (unchanged from the combined file)
# ══════════════════════════════════════════════════════════════════════════════

def _load_product_ids(base: str, headers: dict[str, str]) -> list[int]:
    try:
        response = setup_requests.get(f"{base}/api/products/", headers=headers, timeout=30)
        if response.status_code == 200:
            ids = _extract_product_ids(response.json())
            if ids:
                return ids
    except Exception:
        pass
    return _probe_product_ids(base, headers)


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


def _warm_cache(base: str, headers: dict[str, str], product_ids: list[int]) -> None:
    setup_requests.get(f"{base}/api/products/", headers=headers, timeout=30)
    setup_requests.get(f"{base}/api/products/top-selling/", headers=headers, timeout=30)
    for product_id in product_ids[:50]:
        setup_requests.get(f"{base}/api/products/{product_id}/", headers=headers, timeout=10)
        setup_requests.get(f"{base}/api/products/{product_id}/rating-summary/", headers=headers, timeout=10)


def _print_result_table(environment) -> None:
    stats = environment.stats.total
    total_requests = stats.num_requests
    total_failures = stats.num_failures
    failure_rate = (total_failures / total_requests * 100) if total_requests else 0
    try:
        total_p95 = stats.get_response_time_percentile(0.95)
        total_p99 = stats.get_response_time_percentile(0.99)
    except Exception:
        total_p95 = 0
        total_p99 = 0
    print(f"\nREQ6_RESULTS MODE={LOCUST_MODE}")
    print("-" * 156)
    print(
        f"{'Type':<6} {'Name':<66} {'#Req':>7} {'#Fail':>7} "
        f"{'Fail%':>7} {'Median':>8} {'95%':>8} {'99%':>8} "
        f"{'Avg':>8} {'Min':>8} {'Max':>8} {'AvgSize':>10}"
    )
    print("-" * 156)
    for entry in sorted(environment.stats.entries.values(), key=lambda item: item.name):
        reqs = entry.num_requests
        fails = entry.num_failures
        fail_pct = (fails / reqs * 100) if reqs else 0
        try:
            p95 = entry.get_response_time_percentile(0.95)
            p99 = entry.get_response_time_percentile(0.99)
        except Exception:
            p95 = 0
            p99 = 0
        print(
            f"{entry.method:<6} {entry.name[:66]:<66} {reqs:>7} {fails:>7} "
            f"{fail_pct:>6.2f}% {entry.median_response_time or 0:>8.0f} "
            f"{p95:>8.0f} {p99:>8.0f} {entry.avg_response_time or 0:>8.0f} "
            f"{entry.min_response_time or 0:>8.0f} {entry.max_response_time or 0:>8.0f} "
            f"{entry.avg_content_length or 0:>10.0f}"
        )
    print("-" * 156)
    print(
        f"{'TOTAL':<6} {'Aggregated':<66} {total_requests:>7} {total_failures:>7} "
        f"{failure_rate:>6.2f}% {stats.median_response_time or 0:>8.0f} "
        f"{total_p95:>8.0f} {total_p99:>8.0f} {stats.avg_response_time or 0:>8.0f} "
        f"{stats.min_response_time or 0:>8.0f} {stats.max_response_time or 0:>8.0f} "
        f"{stats.avg_content_length or 0:>10.0f}"
    )
    if SETUP_ERROR:
        print(f"SETUP_ERROR={SETUP_ERROR}")


# ══════════════════════════════════════════════════════════════════════════════
# TEST SETUP
# ══════════════════════════════════════════════════════════════════════════════

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    global ALL_PRODUCT_IDS, SHARED_TOKEN, SETUP_ERROR

    base = environment.host.rstrip("/")

    if IS_BEFORE:
        # ---- BEFORE setup: taken as-is from the standalone BEFORE file ----
        _banner("REQ 6 BEFORE — INITIALIZING CACHE-MISS LOAD TEST")

        print(f"  LOCUST_MODE               : {LOCUST_MODE}")
        print(f"  Active user class          : Req6CacheUser (BEFORE branch)")
        print(f"  Redis host                 : {REQ6_REDIS_HOST}:{REQ6_REDIS_PORT}")
        print(f"  Product list cache key     : {PRODUCT_LIST_CACHE_KEY}")
        print(f"  Product detail key prefix  : {PRODUCT_DETAIL_CACHE_PREFIX}")
        print(f"  Top selling cache key      : {TOP_SELLING_CACHE_KEY}")
        print(f"  Rating summary key prefix  : {RATING_SUMMARY_CACHE_PREFIX}")
        print("")
        print("  BEFORE strategy:")
        print("    - Keep Redis running.")
        print("    - Delete each endpoint cache key before and after the request.")
        print("    - Force DB read / aggregation to produce BEFORE-cache baseline.")
        print("")
        print("  Tested cache targets:")
        print("    1. GET /api/products/")
        print("    2. GET /api/products/<id>/")
        print("    3. GET /api/products/top-selling/")
        print("    4. GET /api/products/<id>/rating-summary/")
        print("")

        token = None

        if ADMIN_TOKEN:
            token = ADMIN_TOKEN
            print("  ✓ Setup probe using ADMIN_TOKEN")
        else:
            for email, password in [
                (LOCUST_EMAIL_TPL.format(i=1), LOCUST_PASSWORD),
                (ADMIN_EMAIL, ADMIN_PASSWORD),
            ]:
                try:
                    resp = setup_requests.post(
                        f"{base}/api/users/login/",
                        json={"email": email, "password": password},
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        token = resp.json().get("token")
                        print(f"  ✓ Setup probe authenticated as {email}")
                        break
                except Exception as exc:
                    print(f"  [WARN] Setup login failed for {email}: {exc}")

        if not token:
            print("  [WARN] Setup authentication failed. No authenticated product requests will run.")
            ALL_PRODUCT_IDS = []
            _banner("TEST STARTING")
            return

        SHARED_TOKEN = token
        headers = _req6_headers(token)

        print("[SETUP] Loading product IDs from /api/products/ ...")
        try:
            response = setup_requests.get(
                f"{base}/api/products/",
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                ALL_PRODUCT_IDS = _extract_product_ids(data)

            if not ALL_PRODUCT_IDS:
                print("  [WARN] Could not parse product IDs from list response. Probing /api/products/<id>/ ...")
                ALL_PRODUCT_IDS = _probe_product_ids(base, headers)

            if ALL_PRODUCT_IDS:
                print(f"  ✓ Products loaded: {len(ALL_PRODUCT_IDS)} IDs")
            else:
                print("  [WARN] No product IDs found. Product detail and rating-summary tasks will be skipped.")

        except Exception as exc:
            ALL_PRODUCT_IDS = []
            print(f"  [WARN] Product setup failed: {exc}")
            print("  [WARN] Product detail and rating-summary tasks will be skipped.")

        # Start with clean keys.
        _clear_product_list_cache()
        _clear_top_selling_cache()
        for product_id in ALL_PRODUCT_IDS[:50]:
            _clear_product_detail_cache(product_id)
            _clear_rating_summary_cache(product_id)

        _banner("TEST STARTING")

    else:
        # ---- AFTER setup: unchanged from the combined file ----
        token = _login_once(base)
        if not token:
            SETUP_ERROR = "authentication_failed"
            return
        SHARED_TOKEN = token
        headers = _req6_headers(token)
        ALL_PRODUCT_IDS = _load_product_ids(base, headers)
        if not ALL_PRODUCT_IDS:
            SETUP_ERROR = "no_product_ids_found"
            return

        _clear_all_req6_cache(ALL_PRODUCT_IDS[:50])
        _warm_cache(base, headers, ALL_PRODUCT_IDS)


# ══════════════════════════════════════════════════════════════════════════════
# TEST TEARDOWN REPORT
# ══════════════════════════════════════════════════════════════════════════════

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    if IS_BEFORE:
        # ---- BEFORE report: taken as-is from the standalone BEFORE file ----
        _banner("REQ 6 BEFORE — CACHE-MISS BASELINE REPORT")

        stats = environment.stats.total
        total_requests = stats.num_requests
        total_failures = stats.num_failures
        failure_rate = (total_failures / total_requests * 100) if total_requests else 0

        try:
            p95 = stats.get_response_time_percentile(0.95)
            p99 = stats.get_response_time_percentile(0.99)
        except Exception:
            p95 = 0
            p99 = 0

        print(f"Mode                  : {LOCUST_MODE}")
        print(f"Total Locust requests : {total_requests}")
        print(f"Total Locust failures : {total_failures}")
        print(f"Failure rate          : {failure_rate:.2f}%")
        print(f"Average response      : {stats.avg_response_time or 0:.0f}ms")
        print(f"Median response       : {stats.median_response_time or 0:.0f}ms")
        print(f"95th percentile       : {p95:.0f}ms")
        print(f"99th percentile       : {p99:.0f}ms")
        print("")

        print("Endpoint averages:")
        print(f"  Product list      : {_avg(req6_product_list_times_ms):.0f}ms | samples={len(req6_product_list_times_ms)}")
        print(f"  Product detail    : {_avg(req6_product_detail_times_ms):.0f}ms | samples={len(req6_product_detail_times_ms)}")
        print(f"  Top selling       : {_avg(req6_top_selling_times_ms):.0f}ms | samples={len(req6_top_selling_times_ms)}")
        print(f"  Rating summary    : {_avg(req6_rating_summary_times_ms):.0f}ms | samples={len(req6_rating_summary_times_ms)}")
        print("")


        print("\n" + "═" * 72 + "\n")

    else:
        # ---- AFTER report: unchanged from the combined file ----
        _print_result_table(environment)


# ══════════════════════════════════════════════════════════════════════════════
# USER CLASS
# ══════════════════════════════════════════════════════════════════════════════

class Req6CacheUser(HttpUser):
    """
    Requirement 6 user.

    BEFORE branch : behavior taken as-is from the standalone BEFORE file
      (clears the relevant Redis key before AND after every request, times
      each request manually, and records it via _record()).

    AFTER branch  : behavior unchanged from the combined file
      (cache stays warm; no per-request clearing or timing).
    """

    wait_time = between(0.2, 1.0)
    weight = 1

    def on_start(self):
        self.token = SHARED_TOKEN

    def _headers(self) -> dict[str, str]:
     return _req6_headers(self.token)

    @task(5)
    def product_list(self):
        if not self.token:
            return

        name = (
            "/api/products/ [REQ6 BEFORE - PRODUCT LIST DB READ]"
            if IS_BEFORE else
            "/api/products/ [REQ6 AFTER - PRODUCT LIST]"
        )

        if IS_BEFORE:
            _clear_product_list_cache()
            started = time.time()

        with self.client.get(
            "/api/products/",
            headers=self._headers(),
            name=name,
            catch_response=True,
        ) as resp:
            if IS_BEFORE:
                elapsed_ms = (time.time() - started) * 1000
                _clear_product_list_cache()

            if resp.status_code == 200:
                if IS_BEFORE:
                    _record("product_list", elapsed_ms)
                resp.success()
            else:
                if IS_BEFORE:
                    _record("product_list", elapsed_ms, failed=True)
                    resp.failure(f"Product list failed: {resp.status_code} {resp.text[:120]}")
                else:
                    resp.failure(f"{resp.status_code} {resp.text[:120]}")

    @task(4)
    def product_detail(self):
        if not self.token or not ALL_PRODUCT_IDS:
            return

        product_id = random.choice(ALL_PRODUCT_IDS)

        name = (
            "/api/products/[id]/ [REQ6 BEFORE - PRODUCT DETAIL DB READ]"
            if IS_BEFORE else
            "/api/products/[id]/ [REQ6 AFTER - PRODUCT DETAIL]"
        )

        if IS_BEFORE:
            _clear_product_detail_cache(product_id)
            started = time.time()

        with self.client.get(
            f"/api/products/{product_id}/",
            headers=self._headers(),
            name=name,
            catch_response=True,
        ) as resp:
            if IS_BEFORE:
                elapsed_ms = (time.time() - started) * 1000
                _clear_product_detail_cache(product_id)

            if resp.status_code == 200:
                if IS_BEFORE:
                    _record("product_detail", elapsed_ms)
                resp.success()
            else:
                if IS_BEFORE:
                    _record("product_detail", elapsed_ms, failed=True)
                    resp.failure(f"Product detail failed: {resp.status_code} {resp.text[:120]}")
                else:
                    resp.failure(f"{resp.status_code} {resp.text[:120]}")

    @task(3)
    def top_selling(self):
        if not self.token:
            return

        name = (
            "/api/products/top-selling/ [REQ6 BEFORE - TOP SELLING DB AGGREGATION]"
            if IS_BEFORE else
            "/api/products/top-selling/ [REQ6 AFTER - TOP SELLING]"
        )

        if IS_BEFORE:
            _clear_top_selling_cache()
            started = time.time()

        with self.client.get(
            "/api/products/top-selling/",
            headers=self._headers(),
            name=name,
            catch_response=True,
        ) as resp:
            if IS_BEFORE:
                elapsed_ms = (time.time() - started) * 1000
                _clear_top_selling_cache()

            if resp.status_code == 200:
                if IS_BEFORE:
                    _record("top_selling", elapsed_ms)
                resp.success()
            else:
                if IS_BEFORE:
                    _record("top_selling", elapsed_ms, failed=True)
                    resp.failure(f"Top selling failed: {resp.status_code} {resp.text[:120]}")
                else:
                    resp.failure(f"{resp.status_code} {resp.text[:120]}")

    @task(3)
    def rating_summary(self):
        if not self.token or not ALL_PRODUCT_IDS:
            return

        product_id = random.choice(ALL_PRODUCT_IDS)

        name = (
            "/api/products/[id]/rating-summary/ [REQ6 BEFORE - RATING DB AGGREGATION]"
            if IS_BEFORE else
            "/api/products/[id]/rating-summary/ [REQ6 AFTER - RATING SUMMARY]"
        )

        if IS_BEFORE:
            _clear_rating_summary_cache(product_id)
            started = time.time()

        with self.client.get(
            f"/api/products/{product_id}/rating-summary/",
            headers=self._headers(),
            name=name,
            catch_response=True,
        ) as resp:
            if IS_BEFORE:
                elapsed_ms = (time.time() - started) * 1000
                _clear_rating_summary_cache(product_id)

            if resp.status_code == 200:
                if IS_BEFORE:
                    _record("rating_summary", elapsed_ms)
                resp.success()
            else:
                if IS_BEFORE:
                    _record("rating_summary", elapsed_ms, failed=True)
                    resp.failure(f"Rating summary failed: {resp.status_code} {resp.text[:120]}")
                else:
                    resp.failure(f"{resp.status_code} {resp.text[:120]}")
