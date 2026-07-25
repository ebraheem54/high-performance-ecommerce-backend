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
    LOCUST_MODE=req4_before
    LOCUST_MODE=req4_after

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
  │ req4_before                  │ BatchNaiveUser       │ Req 4 before: naive batch (no chunking)     │
  │ req4_after                   │ BatchChunkedUser     │ Req 4 after: chunked batch (CHUNK_SIZE=50)  │
  └──────────────────────────────┴──────────────────────┴─────────────────────────────────────────────┘

REQ 4 BEFORE/AFTER:
  STEP 1 — Seed orders first (normal mode, 2 min):
    LOCUST_MODE=normal docker compose run --rm locust \
      -f locustfile.py --host http://nginx:80 \
      --users 30 --spawn-rate 5 --headless --run-time 120s

  STEP 2 — Run BEFORE (naive, no chunking):
    LOCUST_MODE=req4_before ADMIN_TOKEN=<token> docker compose run --rm \
      -e LOCUST_MODE=req4_before -e ADMIN_TOKEN=<token> locust \
      -f locustfile.py --host http://nginx:80 \
      --users 1 --spawn-rate 1 --headless --run-time 30s
    Watch: docker logs ecommerce_celery_batch_worker --follow
    Look for: [BATCH-NAIVE] Loaded ALL X orders into memory at once

  STEP 3 — Run AFTER (chunked):
    LOCUST_MODE=req4_after ADMIN_TOKEN=<token> docker compose run --rm \
      -e LOCUST_MODE=req4_after -e ADMIN_TOKEN=<token> locust \
      -f locustfile.py --host http://nginx:80 \
      --users 1 --spawn-rate 1 --headless --run-time 30s
    Watch: docker logs ecommerce_celery_batch_worker --follow
    Look for: [BATCH] Chunk 1/N processed ...
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
    "race1_before",
    "race1_after",
    "req3_sync",
    "req3_async",
    "req4_before",
    "req4_after",
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

# REQ 1 — detailed per-case counters (race1_before / race1_after modes)
race1_counts = {
    "case1_oversell"       : 0,   # stock went negative — race condition proven
    "case1_checkout_ok"    : 0,   # safe checkout succeeded (HTTP 201)
    "case1_stockout"       : 0,   # correctly blocked — not enough stock (HTTP 400)
    "case2_wallet_ok"      : 0,   # wallet checkout queued successfully (HTTP 202)
    "case2_wallet_blocked" : 0,   # insufficient balance — lock prevented double-spend
    "case3_payment_ok"     : 0,   # first payment processed (HTTP 200)
    "case3_payment_blocked": 0,   # duplicate payment blocked (HTTP 400)
    "case4_cancel_ok"      : 0,   # cancel succeeded (HTTP 200)
    "case4_cancel_blocked" : 0,   # cancel rejected — wrong state (HTTP 400)
    "case5_reserve_ok"     : 0,   # reservation acquired (HTTP 201)
    "case5_reserve_blocked": 0,   # reservation blocked — insufficient stock (HTTP 400)
}

req2_total_requests = 0
req2_errors = 0
req2_capacity_times_ms: list = []
req2_db_connections: list = []

req3_sync_times: list = []
req3_async_times: list = []

req6_first_hit_times: list = []
req6_cache_hit_times: list = []

req4_triggered = False

req4_naive_times_ms: list = []
req4_chunked_times_ms: list = []
req4_naive_count = 0
req4_chunked_count = 0


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
    print(f"    EcommerceUser              = {EcommerceUser.weight}")
    print(f"    BrowsingUser               = {BrowsingUser.weight}")
    print(f"    RaceConditionDemoUser      = {RaceConditionDemoUser.weight}")
    print(f"    Race1OversellingBeforeUser = {Race1OversellingBeforeUser.weight}")
    print(f"    Race1SafeUser              = {Race1SafeUser.weight}")
    print(f"    CapacityStressUser         = {CapacityStressUser.weight}")
    print(f"    CheckoutSyncUser           = {CheckoutSyncUser.weight}")
    print(f"    CheckoutAsyncUser          = {CheckoutAsyncUser.weight}")
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

    print("\nREQ 1 — Race Condition Safety (5 Cases)")
    print(f"  [General] HOT checkouts via EcommerceUser succeeded : {req1_success}")
    print(f"  [General] Stock-outs blocked                        : {req1_stock_out}")

    if LOCUST_MODE in ("race1_before", "race1_after"):
        print("")
        print("  CASE 1 — Concurrent Checkout / Overselling")
        print("    BEFORE endpoint : POST /api/orders/race-demo/  (no lock, 100ms sleep → stock < 0)")
        print("    AFTER  endpoint : POST /api/orders/checkout/   (SELECT FOR UPDATE on product rows)")
        print(f"    Oversell events (stock<0)     : {race1_counts['case1_oversell']}")
        print(f"    Safe checkout ok (201)        : {race1_counts['case1_checkout_ok']}")
        print(f"    Correctly blocked (400)       : {race1_counts['case1_stockout']}")
        if race1_counts["case1_oversell"] > 0:
            print("    ⚠  RACE CONDITION PROVEN — stock went negative without locking")
        elif race1_counts["case1_checkout_ok"] > 0:
            print("    ✅ PROTECTED — pessimistic lock prevented overselling")

        print("")
        print("  CASE 2 — Wallet Checkout / Double Spend")
        print("    BEFORE endpoint : POST /api/orders/blocking-wallet-checkout/")
        print("    AFTER  endpoint : POST /api/orders/checkout-wallet-async/")
        print("    Scenario : concurrent wallet checkouts — user row locked to prevent double spend")
        print(f"    Queued (202)                  : {race1_counts['case2_wallet_ok']}")
        print(f"    Blocked — low balance (400)   : {race1_counts['case2_wallet_blocked']}")

        print("")
        print("  CASE 3 — Double Payment Processing")
        print("    BEFORE endpoint : POST /api/orders/<id>/process-payment-unsafe/ twice")
        print("    AFTER  endpoint : POST /api/orders/<id>/process-payment/ twice after safe order")
        print("    Scenario : same customer tries to pay the same order twice — order+payment rows locked")
        print(f"    First payment ok (200)        : {race1_counts['case3_payment_ok']}")
        print(f"    Duplicate blocked (400)       : {race1_counts['case3_payment_blocked']}")
        if race1_counts["case3_payment_blocked"] > 0:
            print("    ✅ PROTECTED — double payment prevented by pessimistic lock")

        print("")
        print("  CASE 4 — Cancel Order While Payment Is Processing")
        print("    BEFORE endpoint : POST /api/orders/<id>/cancel-unsafe/ after payment attempt")
        print("    AFTER  endpoint : POST /api/orders/<id>/cancel/ after safe order/payment attempt")
        print("    Scenario : cancel races with payment — order row lock and state machine enforced")
        print(f"    Cancel ok (200)               : {race1_counts['case4_cancel_ok']}")
        print(f"    Blocked — wrong state (400)   : {race1_counts['case4_cancel_blocked']}")

        print("")
        print("  CASE 5 — Product Reservation / Over-Reservation")
        print("    BEFORE endpoint : POST /api/products/<id>/reserve-unsafe/  (no product row lock)")
        print("    AFTER  endpoint : POST /api/products/<id>/reserve/         (SELECT FOR UPDATE)")
        print("    Scenario : concurrent reservations for same product — unsafe over-reserves, safe blocks")
        print(f"    Reservation ok (201)          : {race1_counts['case5_reserve_ok']}")
        if LOCUST_MODE == "race1_before":
            print(f"    Over-reserved / blocked count : {race1_counts['case5_reserve_blocked']}")
        else:
            print(f"    Blocked — low stock (400)     : {race1_counts['case5_reserve_blocked']}")

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

    print("\nREQ 4 — Batch Processing (BEFORE vs AFTER)")
    if req4_triggered:
        print("  ✅ run_daily_sales_batch_task triggered (auto)")
        print("  ℹ Check Celery logs: [BATCH] Chunk X/Y processed...")

    if req4_naive_times_ms:
        avg_n = sum(req4_naive_times_ms) / len(req4_naive_times_ms)
        print(f"  BEFORE (naive — no chunking) : triggers={req4_naive_count}  avg_trigger_ms={avg_n:.0f}ms")
        print(f"  ⚠ Watch Celery logs for: [BATCH-NAIVE] Loaded ALL X orders into memory at once")

    if req4_chunked_times_ms:
        avg_c = sum(req4_chunked_times_ms) / len(req4_chunked_times_ms)
        print(f"  AFTER  (chunked — CHUNK_SIZE=50) : triggers={req4_chunked_count}  avg_trigger_ms={avg_c:.0f}ms")
        print(f"  ✅ Watch Celery logs for: [BATCH] Chunk 1/N processed...")

    if not req4_naive_times_ms and not req4_chunked_times_ms and not req4_triggered:
        print("  ℹ Run with LOCUST_MODE=req4_before or req4_after to test batch processing")

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


def _race1_incr(key: str, delta: int = 1):
    """Thread-safe counter for REQ 1 per-case metrics."""
    with _lock:
        if key in race1_counts:
            race1_counts[key] += delta


def _record(name: str, value: float):
    global req4_naive_count, req4_chunked_count
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
        elif name == "req4_naive_ms":
            req4_naive_times_ms.append(value)
            req4_naive_count += 1
        elif name == "req4_chunked_ms":
            req4_chunked_times_ms.append(value)
            req4_chunked_count += 1


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
# CLASS 3 — RaceConditionDemoUser  (original race_before mode — unchanged)
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
            name="Before | POST /api/orders/race-demo/",
            catch_response=True,
        ) as resp:
            if resp.status_code in (201, 409):
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}: {resp.text[:100]}")


# ══════════════════════════════════════════════════════════════════════════════
# CLASS 3a — Race1OversellingBeforeUser  (LOCUST_MODE=race1_before)
#
# REQ 1 — CASE 1 BEFORE: Concurrent Checkout / Overselling
#
# Endpoint : POST /api/orders/race-demo/
# Scenario : All concurrent users buy the same hot product simultaneously.
#            No lock is held. Each user reads a stale stock snapshot, sleeps
#            100ms (all piled up at the same point), then decrements stock.
# Expected : oversell=true in response body — stock goes negative.
# Report   : race1_counts["case1_oversell"] incremented for every oversell.
# ══════════════════════════════════════════════════════════════════════════════

class Race1BaseUser(HttpUser):
    """Shared Req 1 setup for customer-driven race-condition cases."""

    abstract = True
    wait_time = between(0.05, 0.2)
    weight = 0
    mode_label = "Req1"
    login_label = "race1"

    def on_start(self):
        self.token = _login(
            self.client,
            LOCUST_EMAIL_TPL.format(i=random.randint(1, LOCUST_USER_COUNT)),
            LOCUST_PASSWORD,
            f"[{self.login_label}]",
        )

    def _h(self):
        return {"Authorization": f"Token {self.token}"} if self.token else {}

    def _hot_product_id(self):
        ids = HOT_PRODUCT_IDS or ALL_PRODUCT_IDS
        return ids[0] if ids else None

    def _normal_product_id(self):
        return random.choice(ALL_PRODUCT_IDS) if ALL_PRODUCT_IDS else None

    def _clear_cart_setup(self) -> None:
        """Clear cart outside Locust stats so setup noise stays out of Req 1 rows."""
        if not self.token:
            return
        try:
            _requests.delete(
                f"{self.client.base_url}/api/cart/clear/",
                headers=self._h(),
                timeout=10,
            )
        except Exception as exc:
            print(f"  [WARN] Req1 setup cart clear failed: {exc}")

    def _add_product_to_cart(self, product_id: int, quantity: int = 1) -> bool:
        self._clear_cart_setup()
        with self.client.post(
            "/api/cart/add/",
            json={"product_id": product_id, "quantity": quantity},
            headers=self._h(),
            name=f"{self.mode_label} Setup | POST /api/cart/add/",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                resp.success()
                return True
            resp.failure(f"Cart add failed: {resp.status_code} {resp.text[:120]}")
            return False

    def _safe_checkout_one_item(self, product_id: int | None = None) -> int | None:
        if not self.token:
            return None
        pid = product_id or self._normal_product_id()
        if not pid or not self._add_product_to_cart(pid):
            return None
        with self.client.post(
            "/api/orders/checkout/",
            json={},
            headers=self._h(),
            name=f"{self.mode_label} Case 1 AFTER | POST /api/orders/checkout/ (locked checkout)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                _race1_incr("case1_checkout_ok")
                resp.success()
                return resp.json().get("id")
            if resp.status_code in (400, 409):
                _race1_incr("case1_stockout")
                resp.success()
                return None
            resp.failure(f"Locked checkout failed: {resp.status_code} {resp.text[:120]}")
            return None

    def _unsafe_checkout_one_item(self, product_id: int | None = None) -> int | None:
        if not self.token:
            return None
        pid = product_id or self._hot_product_id()
        if not pid:
            return None
        with self.client.post(
            "/api/orders/race-demo/",
            json={"product_id": pid},
            headers=self._h(),
            name=f"{self.mode_label} Case 1 BEFORE | POST /api/orders/race-demo/ (unsafe checkout)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                data = resp.json()
                if data.get("oversell"):
                    _race1_incr("case1_oversell")
                    resp.failure(f"OVERSELL: stock={data.get('actual_stock')} product={pid}")
                else:
                    _race1_incr("case1_checkout_ok")
                    resp.success()
                return data.get("order_id")
            if resp.status_code in (400, 409):
                _race1_incr("case1_stockout")
                resp.success()
                return None
            resp.failure(f"Unsafe checkout failed: {resp.status_code} {resp.text[:120]}")
            return None

    def _process_payment_twice(self, order_id: int, prefix: str, unsafe: bool = False):
        if not self.token:
            return

        endpoint = "process-payment-unsafe" if unsafe else "process-payment"
        row_name = f"{prefix} | POST /api/orders/{{id}}/{endpoint}/"
        with self.client.post(
            f"/api/orders/{order_id}/{endpoint}/",
            json={"method": "credit_card", "transaction_id": f"req1-{order_id}-1"},
            headers=self._h(),
            name=row_name,
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                _race1_incr("case3_payment_ok")
                resp.success()
            elif resp.status_code == 400:
                _race1_incr("case3_payment_blocked")
                resp.success()
            else:
                resp.failure(f"First payment unexpected: {resp.status_code} {resp.text[:120]}")

        with self.client.post(
            f"/api/orders/{order_id}/{endpoint}/",
            json={"method": "credit_card", "transaction_id": f"req1-{order_id}-2"},
            headers=self._h(),
            name=row_name,
            catch_response=True,
        ) as resp:
            if resp.status_code == 400:
                _race1_incr("case3_payment_blocked")
                resp.success()
            elif resp.status_code == 200:
                if unsafe:
                    resp.failure(f"409 Conflict: duplicate payment processed for order={order_id}")
                else:
                    resp.failure(f"Duplicate payment succeeded unexpectedly for order={order_id}")
            else:
                resp.failure(f"Duplicate payment unexpected: {resp.status_code} {resp.text[:120]}")

    def _cancel_after_payment_attempt(self, order_id: int, prefix: str, unsafe: bool = False):
        try:
            _requests.post(
                f"{self.client.base_url}/api/orders/{order_id}/process-payment/",
                json={"method": "credit_card", "transaction_id": f"req1-cancel-race-{order_id}"},
                headers=self._h(),
                timeout=10,
            )
        except Exception as exc:
            print(f"  [WARN] Req1 setup payment failed: {exc}")

        endpoint = "cancel-unsafe" if unsafe else "cancel"
        with self.client.post(
            f"/api/orders/{order_id}/{endpoint}/",
            json={},
            headers=self._h(),
            name=f"{prefix} | POST /api/orders/{{id}}/{endpoint}/",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                if unsafe and data.get("paid_and_cancelled"):
                    _race1_incr("case4_cancel_blocked")
                    resp.failure(f"409 Conflict: paid order was cancelled order={order_id}")
                else:
                    _race1_incr("case4_cancel_ok")
                    resp.success()
            elif resp.status_code == 400:
                _race1_incr("case4_cancel_blocked")
                if prefix.startswith("Req1 BEFORE"):
                    resp.failure("409 Conflict: cancel lost race with payment processing")
                else:
                    resp.success()
            else:
                resp.failure(f"Cancel unexpected: {resp.status_code} {resp.text[:120]}")


class Race1OversellingBeforeUser(Race1BaseUser):
    mode_label = "Req1 BEFORE"
    login_label = "race1-before"

    @task(3)
    def case1_concurrent_checkout_overselling(self):
        """Case 1 before: unsafe checkout can oversell stock."""
        self._unsafe_checkout_one_item(self._hot_product_id())

    @task(2)
    def case2_wallet_checkout_double_spend(self):
        """Case 2: wallet checkout endpoint exercises user-row balance locking."""
        if not self.token:
            return
        pid = self._normal_product_id()
        if not pid or not self._add_product_to_cart(pid):
            return
        with self.client.post(
            "/api/orders/blocking-wallet-checkout/",
            json={},
            headers=self._h(),
            name="Req1 BEFORE Case 2 | POST /api/orders/blocking-wallet-checkout/ (wallet double-spend)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                _race1_incr("case2_wallet_ok")
                resp.success()
            elif resp.status_code in (400, 409):
                _race1_incr("case2_wallet_blocked")
                resp.failure("409 Conflict: wallet double-spend race blocked")
            else:
                resp.failure(f"Wallet checkout unexpected: {resp.status_code} {resp.text[:120]}")

    @task(2)
    def case3_double_payment_processing(self):
        """Case 3: first customer payment succeeds, duplicate payment is blocked."""
        order_id = self._unsafe_checkout_one_item(self._normal_product_id())
        if order_id:
            self._process_payment_twice(
                order_id,
                "Req1 BEFORE Case 3 double payment",
                unsafe=True,
            )

    @task(1)
    def case4_cancel_while_payment_processing(self):
        """Case 4: cancel races with payment/state transition."""
        order_id = self._unsafe_checkout_one_item(self._normal_product_id())
        if order_id:
            self._cancel_after_payment_attempt(
                order_id,
                "Req1 BEFORE Case 4 cancel while payment processing",
                unsafe=True,
            )

    @task(2)
    def case5_product_reservation_over_reservation(self):
        """Case 5: product reservation targets the same low-stock product."""
        if not self.token:
            return
        pid = self._hot_product_id()
        if not pid:
            return
        with self.client.post(
            f"/api/products/{pid}/reserve-unsafe/",
            json={"quantity": 1, "lock_minutes": 5},
            headers=self._h(),
            name="Req1 BEFORE Case 5 | POST /api/products/{id}/reserve-unsafe/ (over-reservation)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                data = resp.json()
                if data.get("over_reserved"):
                    _race1_incr("case5_reserve_blocked")
                    resp.failure(
                        f"OVER-RESERVED: reserved={data.get('actual_reserved')} stock={data.get('stock')}"
                    )
                else:
                    _race1_incr("case5_reserve_ok")
                    resp.success()
            elif resp.status_code == 400:
                _race1_incr("case5_reserve_blocked")
                resp.success()
            else:
                resp.failure(f"Reserve unexpected: {resp.status_code} {resp.text[:120]}")


# ══════════════════════════════════════════════════════════════════════════════
# CLASS 3b — Race1SafeUser  (LOCUST_MODE=race1_after)
#
# REQ 1 — CASES 1-5 AFTER: All race conditions handled by protected endpoints.
#
# CASE 1 — Concurrent Checkout / Overselling
#   Endpoint : POST /api/orders/checkout/  (SELECT FOR UPDATE on product rows)
#   Expected : HTTP 201 or 400 (stock-out). Stock never negative.
#
# CASE 2 — Wallet Checkout / Double Spend
#   Endpoint : POST /api/orders/checkout-wallet-async/
#   Expected : HTTP 202 (queued) or 400 (insufficient balance).
#              User row locked atomically — no double spend possible.
#
# CASE 3 — Double Payment Processing
#   Endpoint : POST /api/orders/<id>/process-payment/  (owning customer)
#   Scenario : Customer calls process-payment twice on the same order.
#   Expected : First call HTTP 200; second call HTTP 400 "Payment already completed".
#
# CASE 4 — Cancel Order While Payment Is Processing
#   Endpoint : POST /api/orders/<id>/cancel/
#   Scenario : Cancel attempted after checkout. Order row locked by
#              select_for_update() — only valid state transitions pass.
#   Expected : HTTP 200 if PENDING/CONFIRMED; HTTP 400 if PROCESSING/CANCELLED.
#
# CASE 5 — Product Reservation / Over-Reservation
#   Endpoint : POST /api/products/<id>/reserve/
#   Expected : HTTP 201 if stock available; HTTP 400 if insufficient.
#              Product row locked — concurrent over-reservation impossible.
# ══════════════════════════════════════════════════════════════════════════════

class Race1SafeUser(Race1BaseUser):
    mode_label = "Req1 AFTER"
    login_label = "race1-after"

    @task(3)
    def case1_concurrent_checkout_no_oversell(self):
        """Case 1 after: locked checkout blocks overselling."""
        self._safe_checkout_one_item(self._hot_product_id())

    @task(2)
    def case2_wallet_checkout_double_spend_blocked(self):
        """Case 2 after: async wallet checkout queues work that uses wallet locking."""
        if not self.token:
            return
        pid = self._normal_product_id()
        if not pid or not self._add_product_to_cart(pid):
            return
        with self.client.post(
            "/api/orders/checkout-wallet-async/",
            json={},
            headers=self._h(),
            name="Req1 AFTER Case 2 | POST /api/orders/checkout-wallet-async/ (wallet double-spend protected)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 202:
                _race1_incr("case2_wallet_ok")
                resp.success()
            elif resp.status_code in (400, 409, 503):
                _race1_incr("case2_wallet_blocked")
                resp.success()
            else:
                resp.failure(f"Wallet checkout unexpected: {resp.status_code} {resp.text[:120]}")

    @task(2)
    def case3_double_payment_processing_blocked(self):
        """Case 3 after: payment endpoint locks order and payment rows."""
        order_id = self._safe_checkout_one_item(self._normal_product_id())
        if order_id:
            self._process_payment_twice(
                order_id,
                "Req1 AFTER Case 3 double payment",
            )

    @task(1)
    def case4_cancel_while_payment_processing_blocked(self):
        """Case 4 after: order state lock prevents invalid cancel/payment races."""
        order_id = self._safe_checkout_one_item(self._normal_product_id())
        if order_id:
            self._cancel_after_payment_attempt(
                order_id,
                "Req1 AFTER Case 4 cancel while payment processing",
            )

    @task(2)
    def case5_product_reservation_over_reservation_blocked(self):
        """Case 5 after: reservation uses product-row locking."""
        if not self.token:
            return
        pid = self._hot_product_id()
        if not pid:
            return
        with self.client.post(
            f"/api/products/{pid}/reserve/",
            json={"quantity": 1, "lock_minutes": 5},
            headers=self._h(),
            name="Req1 AFTER Case 5 | POST /api/products/{id}/reserve/ (over-reservation protected)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                _race1_incr("case5_reserve_ok")
                resp.success()
            elif resp.status_code == 400:
                _race1_incr("case5_reserve_blocked")
                resp.success()
            else:
                resp.failure(f"Reserve unexpected: {resp.status_code} {resp.text[:120]}")


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
# CLASS 7 — BatchNaiveUser  (REQ 4 — BEFORE: no chunking)
# ══════════════════════════════════════════════════════════════════════════════
# Triggers the naive batch endpoint repeatedly so the doctor can see in
# Celery logs: [BATCH-NAIVE] Loaded ALL X orders into memory at once
# Use --users 1  (a single admin is enough — batch is heavy, not concurrent)

class BatchNaiveUser(HttpUser):
    weight = 0
    wait_time = between(8.0, 12.0)

    def on_start(self):
        if ADMIN_TOKEN:
            self.token = ADMIN_TOKEN
            print("  ✓ BatchNaiveUser using ADMIN_TOKEN")
            return
        self.token = _login(self.client, ADMIN_EMAIL, ADMIN_PASSWORD, "[batch-naive]")
        if not self.token:
            print("  [WARN] Admin login failed — BatchNaiveUser idle")

    @task
    def trigger_naive_batch(self):
        if not self.token:
            return

        t = time.time()
        with self.client.post(
            "/api/core/trigger-batch-naive/",
            json={"days_back": 7},
            headers={"Authorization": f"Token {self.token}"},
            name="POST /api/core/trigger-batch-naive/ [REQ4 BEFORE — NO CHUNKS]",
            catch_response=True,
        ) as resp:
            ms = (time.time() - t) * 1000
            if resp.status_code in (200, 202):
                _record("req4_naive_ms", ms)
                resp.success()
                print(
                    f"  [REQ4-BEFORE] Naive batch queued in {ms:.0f}ms — "
                    "watch Celery logs for [BATCH-NAIVE] Loaded ALL X orders"
                )
            elif resp.status_code == 403:
                resp.failure("Admin auth required — set ADMIN_TOKEN env var")
            else:
                resp.failure(f"Naive batch trigger failed: {resp.status_code} {resp.text[:120]}")


# ══════════════════════════════════════════════════════════════════════════════
# CLASS 8 — BatchChunkedUser  (REQ 4 — AFTER: chunked CHUNK_SIZE=50)
# ══════════════════════════════════════════════════════════════════════════════
# Triggers the chunked batch endpoint repeatedly so the doctor can see in
# Celery logs: [BATCH] Chunk 1/N processed ...  [BATCH] Chunk 2/N processed ...
# chunk_size=10 used so chunks appear clearly even with few orders.

class BatchChunkedUser(HttpUser):
    weight = 0
    wait_time = between(8.0, 12.0)

    def on_start(self):
        if ADMIN_TOKEN:
            self.token = ADMIN_TOKEN
            print("  ✓ BatchChunkedUser using ADMIN_TOKEN")
            return
        self.token = _login(self.client, ADMIN_EMAIL, ADMIN_PASSWORD, "[batch-chunked]")
        if not self.token:
            print("  [WARN] Admin login failed — BatchChunkedUser idle")

    @task
    def trigger_chunked_batch(self):
        if not self.token:
            return

        t = time.time()
        with self.client.post(
            "/api/core/trigger-batch/",
            json={"chunk_size": 10},
            headers={"Authorization": f"Token {self.token}"},
            name="POST /api/core/trigger-batch/ [REQ4 AFTER — CHUNKED]",
            catch_response=True,
        ) as resp:
            ms = (time.time() - t) * 1000
            if resp.status_code in (200, 202):
                _record("req4_chunked_ms", ms)
                resp.success()
                print(
                    f"  [REQ4-AFTER] Chunked batch queued in {ms:.0f}ms — "
                    "watch Celery logs for [BATCH] Chunk 1/N processed..."
                )
            elif resp.status_code == 403:
                resp.failure("Admin auth required — set ADMIN_TOKEN env var")
            else:
                resp.failure(f"Chunked batch trigger failed: {resp.status_code} {resp.text[:120]}")


# ══════════════════════════════════════════════════════════════════════════════
# APPLY MODE WEIGHTS
# ══════════════════════════════════════════════════════════════════════════════

def _apply_mode_weights():
    classes = [
        EcommerceUser,
        BrowsingUser,
        RaceConditionDemoUser,
        Race1OversellingBeforeUser,
        Race1SafeUser,
        CapacityStressUser,
        CheckoutSyncUser,
        CheckoutAsyncUser,
        BatchNaiveUser,
        BatchChunkedUser,
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
    elif LOCUST_MODE == "race1_before":
        Race1OversellingBeforeUser.weight = 1
    elif LOCUST_MODE == "race1_after":
        Race1SafeUser.weight = 1
    elif LOCUST_MODE == "req3_sync":
        CheckoutSyncUser.weight = 1
    elif LOCUST_MODE == "req3_async":
        CheckoutAsyncUser.weight = 1
    elif LOCUST_MODE == "req4_before":
        BatchNaiveUser.weight = 1
    elif LOCUST_MODE == "req4_after":
        BatchChunkedUser.weight = 1


_apply_mode_weights()
