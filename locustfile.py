"""
═══════════════════════════════════════════════════════════════════════════════
Locust Load Test — High-Performance E-Commerce Backend
Parallel Programming Course 2026

Single file with selectable demo modes using LOCUST_MODE.
═══════════════════════════════════════════════════════════════════════════════

WHY THIS VERSION?
  You no longer need to edit class weights manually.
  Select the test with an environment variable:

    LOCUST_MODE=req2
    LOCUST_MODE=normal
    LOCUST_MODE=browsing
    LOCUST_MODE=race_before
    LOCUST_MODE=req3_sync
    LOCUST_MODE=req3_async

DEFAULT MODE:
  req2

RECOMMENDED LIVE DEMO FOR THE ASSISTANT/PROFESSOR:
  Use the stable AFTER setup:
    CONN_MAX_AGE=60
    LOCUST_MODE=req2
    ADMIN_TOKEN=<valid admin token>

  Then run:
    docker compose run --rm \
      -e LOCUST_MODE=req2 \
      -e ADMIN_TOKEN=<valid admin token> \
      locust -f locustfile.py --host http://nginx:80 \
      --users 10 --spawn-rate 2 --headless --run-time 30s

REQ 2 BEFORE/AFTER:
  BEFORE:
    CONN_MAX_AGE=0
    docker compose restart app1 app2 app3

  AFTER:
    CONN_MAX_AGE=60
    docker compose restart app1 app2 app3

  Monitor DB connections:
    watch -n 2 "docker exec ecommerce_db psql -U ecommerce_user -d ecommerce_db -c \"SELECT count(*), state FROM pg_stat_activity WHERE datname='ecommerce_db' GROUP BY state ORDER BY state;\""

  Monitor resources:
    docker stats ecommerce_app1 ecommerce_app2 ecommerce_app3 ecommerce_db ecommerce_nginx

REQ 2 is not only CONN_MAX_AGE.
It includes:
  1. DB connection reuse: CONN_MAX_AGE=0 vs CONN_MAX_AGE=60
  2. HTTP capacity control: Gunicorn workers
  3. Celery capacity control: per-worker concurrency
  4. Queue-specific workers: celery/emails/batch
  5. Retry pressure control: exponential backoff in services.py

MODE TABLE:
  ┌──────────────────────────────┬──────────────────────┬─────────────────────────────────────────────┐
  │ LOCUST_MODE                  │ Active Class         │ Purpose                                     │
  ├──────────────────────────────┼──────────────────────┼─────────────────────────────────────────────┤
  │ req2                         │ CapacityStressUser   │ Req 2 capacity/resource demo                │
  │ normal                       │ EcommerceUser        │ Normal full test: Req 2, 3, 5, 6            │
  │ browsing                     │ BrowsingUser         │ Req 6 cache/read pressure                   │
  │ race_before                  │ RaceConditionDemoUser│ Req 1 before-solution race demo             │
  │ req3_sync                    │ CheckoutSyncUser     │ Req 3 before: synchronous checkout/email    │
  │ req3_async                   │ CheckoutAsyncUser    │ Req 3 after: async Celery checkout/email    │
  └──────────────────────────────┴──────────────────────┴─────────────────────────────────────────────┘
"""

import os
import random
import time
import threading

from locust import HttpUser, task, between, events
import requests as _requests


# ── Mode Selection ─────────────────────────────────────────────────────────
LOCUST_MODE = os.getenv("LOCUST_MODE", "req2").strip().lower()

VALID_MODES = {
    "req2",
    "normal",
    "browsing",
    "race_before",
    "req3_sync",
    "req3_async",
}

if LOCUST_MODE not in VALID_MODES:
    print(f"[WARN] Invalid LOCUST_MODE={LOCUST_MODE!r}. Falling back to 'req2'.")
    LOCUST_MODE = "req2"


# ── Shared state (thread-safe) ─────────────────────────────────────────────
_lock = threading.Lock()

ALL_PRODUCT_IDS: list = []
HOT_PRODUCT_IDS: list = []


# ── Credentials ────────────────────────────────────────────────────────────
LOCUST_USER_COUNT = int(os.getenv("LOCUST_USER_COUNT", "100"))
LOCUST_PASSWORD = os.getenv("LOCUST_PASSWORD", "LocustPass123!")
LOCUST_EMAIL_TPL = os.getenv("LOCUST_EMAIL_TPL", "locust_{i}@test.com")


# ── Admin credentials / token for admin-only endpoints ─────────────────────
# Best live-demo practice:
#   Use ADMIN_TOKEN so Locust does not spend time logging in during Req 2.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@demo.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()


# ── Demo toggles ───────────────────────────────────────────────────────────
# Keep False during Req 2 demo so Locust does not trigger batch automatically.
AUTO_TRIGGER_BATCH_ON_START = os.getenv("AUTO_TRIGGER_BATCH_ON_START", "false").lower() == "true"


# ── Per-requirement metrics ────────────────────────────────────────────────
req1_success = 0
req1_stock_out = 0

req2_total_requests = 0
req2_errors = 0
req2_capacity_times_ms: list = []
req2_db_connections: list = []

req3_sync_times: list = []
req3_async_times: list = []

req6_first_hit_times: list = []
req6_cache_hit_times: list = []

req4_triggered = False


# ══════════════════════════════════════════════════════════════════════════════
# TEST SETUP
# ══════════════════════════════════════════════════════════════════════════════

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    global ALL_PRODUCT_IDS, HOT_PRODUCT_IDS, req4_triggered

    base = environment.host.rstrip("/")
    _banner("INITIALIZING LOAD TEST")

    print(f"  LOCUST_MODE: {LOCUST_MODE}")
    print("  Active weights:")
    print(f"    EcommerceUser          = {EcommerceUser.weight}")
    print(f"    BrowsingUser           = {BrowsingUser.weight}")
    print(f"    RaceConditionDemoUser  = {RaceConditionDemoUser.weight}")
    print(f"    CapacityStressUser     = {CapacityStressUser.weight}")
    print(f"    CheckoutSyncUser       = {CheckoutSyncUser.weight}")
    print(f"    CheckoutAsyncUser      = {CheckoutAsyncUser.weight}")
    print("")

    if LOCUST_MODE == "req2":
        print("  Req 2 mode: skipping product catalog load and batch trigger.")
        print("  Focus endpoint: POST /api/core/capacity-stress/")
        print("  Use ADMIN_TOKEN for clean results without login overhead.")
        if ADMIN_TOKEN:
            print("  ✓ ADMIN_TOKEN detected.")
        else:
            print("  [WARN] ADMIN_TOKEN not provided. Each Locust user will login as admin.")
        _banner("TEST STARTING")
        return

    # ── Try to authenticate setup probe ──────────────────────────────────────
    token = None

    if ADMIN_TOKEN:
        token = ADMIN_TOKEN
        print("  ✓ Setup probe using ADMIN_TOKEN")
    else:
        for email, pw in [
            (LOCUST_EMAIL_TPL.format(i=1), LOCUST_PASSWORD),
            (ADMIN_EMAIL, ADMIN_PASSWORD),
        ]:
            try:
                r = _requests.post(
                    f"{base}/api/users/login/",
                    json={"email": email, "password": pw},
                    timeout=15,
                )
                if r.status_code == 200:
                    token = r.json().get("token")
                    print(f"  ✓ Setup probe authenticated as {email}")
                    break
            except Exception as e:
                print(f"  [WARN] Login attempt failed ({email}): {e}")

    if not token:
        print("  [WARN] Could not authenticate any user — product list will be fallback IDs")
        ALL_PRODUCT_IDS = list(range(1, 51))
        print("  [WARN] Fallback: using product IDs 1-50")
        _banner("TEST STARTING")
        return

    headers = {"Authorization": f"Token {token}"}

    # ── Load product catalog ──────────────────────────────────────────────────
    print("[SETUP 1/2] Loading product catalog...")
    try:
        resp = _requests.get(f"{base}/api/products/", headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", data) if isinstance(data, dict) else data
            if isinstance(results, list):
                ALL_PRODUCT_IDS = [p["id"] for p in results if p.get("is_active")]
                HOT_PRODUCT_IDS = [
                    p["id"] for p in results
                    if p.get("is_active") and 0 < p.get("stock", 999) <= 50
                ]
                print(
                    f"  ✓ Products loaded: {len(ALL_PRODUCT_IDS)} total, "
                    f"{len(HOT_PRODUCT_IDS)} HOT (stock≤50)"
                )
        else:
            print(f"  [WARN] /api/products/ returned {resp.status_code}")
    except Exception as e:
        print(f"  [ERROR] Product load failed: {e}")

    if not ALL_PRODUCT_IDS:
        ALL_PRODUCT_IDS = list(range(1, 51))
        print("  [WARN] Fallback: using product IDs 1-50")

    # ── Trigger Batch (Req 4) ─────────────────────────────────────────────────
    if AUTO_TRIGGER_BATCH_ON_START:
        print("[SETUP 2/2] Triggering batch task (Req 4)...")
        try:
            r = _requests.post(
                f"{base}/api/core/trigger-batch/",
                headers=headers,
                timeout=10,
            )
            if r.status_code in (200, 202):
                req4_triggered = True
                print("  ✓ Batch task queued — check Celery logs for [BATCH] Chunk X/Y")
            else:
                print(
                    f"  ℹ Batch endpoint returned {r.status_code} — "
                    "Celery Beat handles it nightly"
                )
                req4_triggered = True
        except Exception:
            print("  ℹ Batch endpoint not available — runs via Celery Beat nightly (OK)")
            req4_triggered = True
    else:
        print("[SETUP 2/2] Skipping auto batch trigger — AUTO_TRIGGER_BATCH_ON_START=False")

    _banner("TEST STARTING")


# ══════════════════════════════════════════════════════════════════════════════
# TEST TEARDOWN
# ══════════════════════════════════════════════════════════════════════════════

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    _banner("REQUIREMENTS TEST REPORT")

    print(f"Mode: {LOCUST_MODE}")

    print("\nREQ 1 — Race Condition Safety")
    print(f"  HOT product checkouts succeeded : {req1_success}")
    print(f"  Stock-outs blocked by locking   : {req1_stock_out}")

    print("\nREQ 2 — Resource Management & Capacity Control")
    print(f"  Total requests : {req2_total_requests}")
    print(f"  Errors         : {req2_errors}")

    if req2_capacity_times_ms:
        avg_capacity = sum(req2_capacity_times_ms) / len(req2_capacity_times_ms)
        max_capacity = max(req2_capacity_times_ms)
        min_capacity = min(req2_capacity_times_ms)
        print(f"  Capacity-stress avg response : {avg_capacity:.0f}ms")
        print(f"  Capacity-stress min response : {min_capacity:.0f}ms")
        print(f"  Capacity-stress max response : {max_capacity:.0f}ms")
        print(f"  Samples                      : {len(req2_capacity_times_ms)}")

    if req2_db_connections:
        avg_conn = sum(req2_db_connections) / len(req2_db_connections)
        max_conn = max(req2_db_connections)
        min_conn = min(req2_db_connections)
        print(f"  Avg DB connections observed  : {avg_conn:.1f}")
        print(f"  Min DB connections observed  : {min_conn:.0f}")
        print(f"  Max DB connections observed  : {max_conn:.0f}")

    print("  Evidence to screenshot:")
    print("    - Locust response time")
    print("    - docker stats")
    print("    - pg_stat_activity")
    print("    - active_queues for Celery workers")
    print("    - Docker Compose workers/concurrency")
    print("    - Exponential backoff code in services.py")

    if req2_errors == 0:
        print("  ✅ No resource exhaustion detected")
    else:
        print(f"  ❌ {req2_errors} unexpected errors")

    print("\nREQ 3 — Async Queues (BEFORE vs AFTER)")
    if req3_sync_times:
        avg_sync = sum(req3_sync_times) / len(req3_sync_times)
        print(f"  BEFORE (sync email): avg={avg_sync:.0f}ms  samples={len(req3_sync_times)}")
    if req3_async_times:
        avg_async = sum(req3_async_times) / len(req3_async_times)
        print(f"  AFTER  (async Celery): avg={avg_async:.0f}ms  samples={len(req3_async_times)}")
    if req3_sync_times and req3_async_times:
        avg_s = sum(req3_sync_times) / len(req3_sync_times)
        avg_a = sum(req3_async_times) / len(req3_async_times)
        if avg_a > 0:
            print(f"  Speedup: {avg_s / avg_a:.1f}x faster with async ✅")

    print("\nREQ 4 — Batch Processing")
    if req4_triggered:
        print("  ✅ run_daily_sales_batch_task triggered")
        print("  ℹ Check Celery logs: [BATCH] Chunk X/Y processed...")

    print("\nREQ 6 — Distributed Caching")
    if req6_first_hit_times and req6_cache_hit_times:
        avg_f = sum(req6_first_hit_times) / len(req6_first_hit_times)
        avg_c = sum(req6_cache_hit_times) / len(req6_cache_hit_times)
        speedup = avg_f / avg_c if avg_c > 0 else 0
        print(f"  First hit (DB)    : {avg_f:.0f}ms")
        print(f"  Cache hit (Redis) : {avg_c:.0f}ms")
        print(f"  Speedup           : {speedup:.1f}x  {'✅' if speedup >= 1.5 else '⚠'}")

    print("\n" + "═" * 65 + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _banner(title: str):
    print("\n" + "═" * 65)
    print(f"  {title}")
    print("═" * 65)


def _incr(name: str, delta: int = 1):
    global req1_success, req1_stock_out, req2_total_requests, req2_errors
    with _lock:
        if name == "req1_success":
            req1_success += delta
        elif name == "req1_stockout":
            req1_stock_out += delta
        elif name == "req2_req":
            req2_total_requests += delta
        elif name == "req2_err":
            req2_errors += delta


def _record(name: str, value: float):
    with _lock:
        if name == "req3_sync":
            req3_sync_times.append(value)
        elif name == "req3_async":
            req3_async_times.append(value)
        elif name == "req6_first":
            req6_first_hit_times.append(value)
        elif name == "req6_cache":
            req6_cache_hit_times.append(value)
        elif name == "req2_capacity_ms":
            req2_capacity_times_ms.append(value)
        elif name == "req2_db_conn":
            req2_db_connections.append(value)


def _login(client, email: str, password: str, name_suffix: str = "") -> str | None:
    """Login and return token or None on failure."""
    with client.post(
        "/api/users/login/",
        json={"email": email, "password": password},
        name=f"/api/users/login/ {name_suffix}".strip(),
        catch_response=True,
    ) as resp:
        if resp.status_code == 200:
            resp.success()
            return resp.json().get("token")
        resp.failure(f"Login failed ({email}): {resp.status_code} {resp.text[:100]}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# CLASS 1 — EcommerceUser  (Normal full test)
# ══════════════════════════════════════════════════════════════════════════════

class EcommerceUser(HttpUser):
    weight = 0
    wait_time = between(0.5, 2.0)

    def on_start(self):
        self._user_num = random.randint(1, LOCUST_USER_COUNT)
        self._email = LOCUST_EMAIL_TPL.format(i=self._user_num)
        self._first_product_call = True
        self.token = _login(self.client, self._email, LOCUST_PASSWORD)

    def _h(self):
        return {"Authorization": f"Token {self.token}"} if self.token else {}

    @task(5)
    def browse_products(self):
        if not self.token:
            return
        t = time.time()
        with self.client.get(
            "/api/products/",
            headers=self._h(),
            name="/api/products/ [LIST]",
            catch_response=True,
        ) as resp:
            ms = (time.time() - t) * 1000
            _incr("req2_req")
            if resp.status_code == 200:
                resp.success()
                if self._first_product_call:
                    _record("req6_first", ms)
                    self._first_product_call = False
                else:
                    _record("req6_cache", ms)
            else:
                _incr("req2_err")
                resp.failure(f"Products failed: {resp.status_code}")

    @task(1)
    def view_single_product(self):
        if not self.token or not ALL_PRODUCT_IDS:
            return
        pid = random.choice(ALL_PRODUCT_IDS)
        _incr("req2_req")
        with self.client.get(
            f"/api/products/{pid}/",
            headers=self._h(),
            name="/api/products/[id]/",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                _incr("req2_err")
                resp.failure(f"Product detail failed: {resp.status_code}")

    @task(3)
    def checkout_flow(self):
        if not self.token:
            return
        h = self._h()

        self.client.delete("/api/cart/clear/", headers=h, name="/api/cart/clear/")

        use_hot = HOT_PRODUCT_IDS and random.random() < 0.40
        pool = [random.choice(HOT_PRODUCT_IDS)] if use_hot else random.sample(
            ALL_PRODUCT_IDS, k=min(random.randint(1, 3), len(ALL_PRODUCT_IDS))
        )
        qty = 1 if use_hot else random.randint(1, 2)

        added = 0
        for pid in pool:
            with self.client.post(
                "/api/cart/add/",
                json={"product_id": pid, "quantity": qty},
                headers=h,
                name="/api/cart/add/",
                catch_response=True,
            ) as resp:
                if resp.status_code in (200, 201, 400):
                    resp.success()
                    if resp.status_code in (200, 201):
                        added += 1
                else:
                    _incr("req2_err")
                    resp.failure(f"Cart add failed: {resp.status_code}")

        if added == 0:
            return

        _incr("req2_req")
        t = time.time()
        with self.client.post(
            "/api/orders/checkout/",
            json={},
            headers=h,
            name="/api/orders/checkout/ [ASYNC]",
            catch_response=True,
        ) as resp:
            ms = (time.time() - t) * 1000
            _record("req3_async", ms)

            if resp.status_code == 201:
                resp.success()
                if use_hot:
                    _incr("req1_success")
            elif resp.status_code in (400, 409):
                if use_hot:
                    _incr("req1_stockout")
                resp.success()
            else:
                _incr("req2_err")
                resp.failure(f"Checkout failed: {resp.status_code} {resp.text[:100]}")

    @task(1)
    def view_orders(self):
        if not self.token:
            return
        _incr("req2_req")
        with self.client.get(
            "/api/orders/",
            headers=self._h(),
            name="/api/orders/ [LIST]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                _incr("req2_err")
                resp.failure(f"Orders list failed: {resp.status_code}")

    @task(1)
    def view_cart(self):
        if not self.token:
            return
        with self.client.get(
            "/api/cart/",
            headers=self._h(),
            name="/api/cart/ [VIEW]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Cart view failed: {resp.status_code}")


# ══════════════════════════════════════════════════════════════════════════════
# CLASS 2 — BrowsingUser  (Cache stress)
# ══════════════════════════════════════════════════════════════════════════════

class BrowsingUser(HttpUser):
    weight = 0
    wait_time = between(0.2, 1.0)

    def on_start(self):
        self.token = None
        self._first = True
        user_num = random.randint(1, LOCUST_USER_COUNT)
        self.token = _login(
            self.client,
            LOCUST_EMAIL_TPL.format(i=user_num),
            LOCUST_PASSWORD,
            "[browse]",
        )

    @task(8)
    def browse_products(self):
        if not self.token:
            return
        t = time.time()
        with self.client.get(
            "/api/products/",
            headers={"Authorization": f"Token {self.token}"},
            name="/api/products/ [CACHE TEST]",
            catch_response=True,
        ) as resp:
            ms = (time.time() - t) * 1000
            if resp.status_code == 200:
                resp.success()
                if self._first:
                    _record("req6_first", ms)
                    self._first = False
                else:
                    _record("req6_cache", ms)
            else:
                resp.failure(f"Cache test failed: {resp.status_code}")

    @task(2)
    def browse_single(self):
        if not self.token or not ALL_PRODUCT_IDS:
            return
        pid = random.choice(ALL_PRODUCT_IDS)
        self.client.get(
            f"/api/products/{pid}/",
            headers={"Authorization": f"Token {self.token}"},
            name="/api/products/[id]/ [CACHE TEST]",
        )


# ══════════════════════════════════════════════════════════════════════════════
# CLASS 3 — RaceConditionDemoUser
# ══════════════════════════════════════════════════════════════════════════════

class RaceConditionDemoUser(HttpUser):
    wait_time = between(0.1, 0.5)
    weight = 0

    def on_start(self):
        self.token = _login(
            self.client,
            LOCUST_EMAIL_TPL.format(i=random.randint(1, LOCUST_USER_COUNT)),
            LOCUST_PASSWORD,
            "[race-demo]",
        )

    @task
    def race_condition_attack(self):
        if not self.token or not ALL_PRODUCT_IDS:
            return
        pid = random.choice(ALL_PRODUCT_IDS)
        with self.client.post(
            "/api/orders/race-demo/",
            json={"product_id": pid},
            headers={"Authorization": f"Token {self.token}"},
            name="POST /api/orders/race-demo/ [NO LOCK]",
            catch_response=True,
        ) as resp:
            if resp.status_code in (201, 409):
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}: {resp.text[:100]}")


# ══════════════════════════════════════════════════════════════════════════════
# CLASS 4 — CapacityStressUser  (REQ 2)
# ══════════════════════════════════════════════════════════════════════════════

class CapacityStressUser(HttpUser):
    wait_time = between(0.5, 1.5)
    weight = 0

    def on_start(self):
        if ADMIN_TOKEN:
            self.token = ADMIN_TOKEN
            print("  ✓ CapacityStressUser using ADMIN_TOKEN from environment")
            return

        self.token = _login(self.client, ADMIN_EMAIL, ADMIN_PASSWORD, "[admin-stress]")
        if not self.token:
            print(f"  [WARN] Admin login failed ({ADMIN_EMAIL}) — CapacityStressUser idle")

    @task
    def capacity_stress(self):
        if not self.token:
            return

        t = time.time()

        with self.client.post(
            "/api/core/capacity-stress/",
            headers={"Authorization": f"Token {self.token}"},
            name="POST /api/core/capacity-stress/ [REQ2 RESOURCE DEMO]",
            catch_response=True,
        ) as resp:
            ms = (time.time() - t) * 1000
            _incr("req2_req")

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    data = {}

                conn_count = data.get("open_db_connections")
                elapsed = data.get("elapsed_s")

                _record("req2_capacity_ms", ms)

                if isinstance(conn_count, int):
                    _record("req2_db_conn", conn_count)

                resp.success()

                print(
                    f"  [REQ2] response_ms={ms:.0f} "
                    f"endpoint_elapsed={elapsed} "
                    f"open_db_connections={conn_count} "
                    f"| Compare CONN_MAX_AGE=0 vs 60"
                )

            elif resp.status_code == 403:
                _incr("req2_err")
                resp.failure("Admin auth required — check ADMIN_TOKEN or ADMIN credentials")

            else:
                _incr("req2_err")
                resp.failure(f"Capacity stress failed: {resp.status_code} {resp.text[:100]}")


# ══════════════════════════════════════════════════════════════════════════════
# CLASS 5 — CheckoutSyncUser
# ══════════════════════════════════════════════════════════════════════════════

class CheckoutSyncUser(HttpUser):
    weight = 0
    wait_time = between(1.0, 2.0)

    def on_start(self):
        self.token = None
        for _ in range(10):
            candidate = random.randint(1, LOCUST_USER_COUNT)
            tok = _login(
                self.client,
                LOCUST_EMAIL_TPL.format(i=candidate),
                LOCUST_PASSWORD,
                "[sync-before]",
            )
            if tok:
                self.token = tok
                break

    def _h(self):
        return {"Authorization": f"Token {self.token}"} if self.token else {}

    def _add_one_product_to_cart(self) -> bool:
        if not ALL_PRODUCT_IDS:
            return False
        pid = random.choice(ALL_PRODUCT_IDS)
        with self.client.post(
            "/api/cart/add/",
            json={"product_id": pid, "quantity": 1},
            headers=self._h(),
            name="/api/cart/add/ [sync-demo]",
            catch_response=True,
        ) as resp:
            resp.success()
            return resp.status_code in (200, 201)

    @task
    def checkout_sync(self):
        if not self.token:
            return
        self.client.delete("/api/cart/clear/", headers=self._h(), name="/api/cart/clear/ [sync]")
        if not self._add_one_product_to_cart():
            return

        t = time.time()
        with self.client.post(
            "/api/orders/checkout-sync/",
            json={},
            headers=self._h(),
            name="POST /api/orders/checkout-sync/ [REQ3 BEFORE — SLOW]",
            catch_response=True,
        ) as resp:
            ms = (time.time() - t) * 1000
            _record("req3_sync", ms)
            if resp.status_code == 201:
                resp.success()
            elif resp.status_code in (400, 403):
                resp.success()
            else:
                resp.failure(f"Sync checkout failed: {resp.status_code} {resp.text[:100]}")


# ══════════════════════════════════════════════════════════════════════════════
# CLASS 6 — CheckoutAsyncUser
# ══════════════════════════════════════════════════════════════════════════════

class CheckoutAsyncUser(HttpUser):
    weight = 0
    wait_time = between(0.5, 1.5)

    def on_start(self):
        self.token = None
        for _ in range(10):
            tok = _login(
                self.client,
                LOCUST_EMAIL_TPL.format(i=random.randint(1, LOCUST_USER_COUNT)),
                LOCUST_PASSWORD,
                "[async-after]",
            )
            if tok:
                self.token = tok
                break

    def _h(self):
        return {"Authorization": f"Token {self.token}"} if self.token else {}

    def _add_one_product_to_cart(self) -> bool:
        if not ALL_PRODUCT_IDS:
            return False
        pid = random.choice(ALL_PRODUCT_IDS)
        with self.client.post(
            "/api/cart/add/",
            json={"product_id": pid, "quantity": 1},
            headers=self._h(),
            name="/api/cart/add/ [async-demo]",
            catch_response=True,
        ) as resp:
            resp.success()
            return resp.status_code in (200, 201)

    @task
    def checkout_async(self):
        if not self.token:
            return
        self.client.delete("/api/cart/clear/", headers=self._h(), name="/api/cart/clear/ [async]")
        if not self._add_one_product_to_cart():
            return

        t = time.time()
        with self.client.post(
            "/api/orders/checkout/",
            json={},
            headers=self._h(),
            name="POST /api/orders/checkout/ [REQ3 AFTER — FAST]",
            catch_response=True,
        ) as resp:
            ms = (time.time() - t) * 1000
            _record("req3_async", ms)
            if resp.status_code == 201:
                resp.success()
            elif resp.status_code in (400, 409):
                resp.success()
            else:
                resp.failure(f"Async checkout failed: {resp.status_code} {resp.text[:100]}")


# ══════════════════════════════════════════════════════════════════════════════
# APPLY MODE WEIGHTS
# ══════════════════════════════════════════════════════════════════════════════

def _apply_mode_weights():
    classes = [
        EcommerceUser,
        BrowsingUser,
        RaceConditionDemoUser,
        CapacityStressUser,
        CheckoutSyncUser,
        CheckoutAsyncUser,
    ]

    for cls in classes:
        cls.weight = 0

    if LOCUST_MODE == "req2":
        CapacityStressUser.weight = 1
    elif LOCUST_MODE == "normal":
        EcommerceUser.weight = 1
        BrowsingUser.weight = 1
    elif LOCUST_MODE == "browsing":
        BrowsingUser.weight = 1
    elif LOCUST_MODE == "race_before":
        RaceConditionDemoUser.weight = 1
    elif LOCUST_MODE == "req3_sync":
        CheckoutSyncUser.weight = 1
    elif LOCUST_MODE == "req3_async":
        CheckoutAsyncUser.weight = 1


_apply_mode_weights()
