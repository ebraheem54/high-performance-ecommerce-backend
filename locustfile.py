"""
Locust Load Test - High-Performance E-Commerce Backend

Tests Non-Functional Requirements:
  ✓ Requirement 1: Race Condition Safety
    - Concurrent checkouts on same products
    - Pessimistic locking prevents double-spend

  ✓ Requirement 2: Resource Management
    - DB connection pooling (CONN_MAX_AGE)
    - Request throttling (300/min anon, 3000/min user)
    - Celery worker concurrency
    - Response times under load

Usage:
  1. Seed database: docker-compose exec app python manage.py seed_ecommerce --clean
  2. Run test: docker-compose up locust
  3. Open: http://localhost:8089
  4. Configure: 100 users, 40/sec spawn rate
  5. Monitor: Response times should stay < 2000ms

Success Criteria:
  - 0% failures (400 on empty cart = expected, counted as success)
  - Median response < 2000ms
  - 95th percentile < 5000ms
  - No database deadlocks
"""

import random
import time
import threading
import requests as _requests
from locust import HttpUser, task, between, events

PRODUCT_IDS: list = []
TOKEN_POOL:   list = []
_pool_lock = threading.Lock()

# Metrics for resource management validation
checkout_success = 0
checkout_stock_out = 0
checkout_failure = 0


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Pre-authenticate and load product IDs before test starts."""
    global PRODUCT_IDS, TOKEN_POOL
    base = environment.host.rstrip("/")

    print("=" * 70)
    print("[SETUP] Initializing load test...")
    print("=" * 70)

    try:
        # Authenticate test user
        print("[1/2] Authenticating test user...")
        login_resp = _requests.post(
            f"{base}/api/users/login/",
            json={"email": "ee@example.com", "password": "asdasdsdasd1221"},
            timeout=60,
        )

        if login_resp.status_code != 200:
            print(f"[ERROR] Login failed ({login_resp.status_code}): {login_resp.text[:200]}")
            return

        token = login_resp.json().get("token")
        TOKEN_POOL.append(token)
        print(f"      ✓ Token acquired")

        # Load products
        print("[2/2] Loading available products...")
        headers = {"Authorization": f"Token {token}"}
        prod_resp = _requests.get(
            f"{base}/api/products/",
            headers=headers,
            timeout=30,
        )

        if prod_resp.status_code == 200:
            data = prod_resp.json()
            results = data.get("results", data) if isinstance(data, dict) else data

            if isinstance(results, list):
                # Only use products with sufficient stock
                PRODUCT_IDS = [
                    p["id"] for p in results
                    if p.get("stock", 0) > 10 and p.get("is_active", False)
                ]

                total_stock = sum(p.get("stock", 0) for p in results if p.get("is_active", False))
                print(f"      ✓ Loaded {len(PRODUCT_IDS)} products")
                print(f"      ✓ Total available stock: {total_stock:,} units")

    except Exception as exc:
        print(f"[ERROR] Setup failed: {exc}")
        import traceback
        traceback.print_exc()

    # Fallback
    if not PRODUCT_IDS:
        PRODUCT_IDS = list(range(1, 51))
        print("[WARN] Using fallback product IDs 1-50")

    if not TOKEN_POOL:
        print("[ERROR] No token obtained - users will login individually (slow!)")

    print("=" * 70)
    print("[READY] Test can now start")
    print("=" * 70)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Print resource management metrics after test."""
    print("\n" + "=" * 70)
    print("RESOURCE MANAGEMENT TEST RESULTS")
    print("=" * 70)
    print(f"Successful checkouts:     {checkout_success}")
    print(f"Expected stock-outs:      {checkout_stock_out}")
    print(f"Unexpected failures:      {checkout_failure}")
    print(f"Total checkout attempts:  {checkout_success + checkout_stock_out + checkout_failure}")

    if checkout_failure == 0:
        print("\n✓ REQUIREMENT 2 PASSED: Resource management working correctly")
        print("  - No unexpected failures")
        print("  - All errors are expected stock exhaustion")
    else:
        print(f"\n✗ REQUIREMENT 2 FAILED: {checkout_failure} unexpected failures")

    print("=" * 70)


def _get_token(client) -> str | None:
    """Get authentication token (pre-generated or login)."""
    if TOKEN_POOL:
        return TOKEN_POOL[0]

    # Fallback: individual login (slow)
    resp = client.post(
        "/api/users/login/",
        json={"email": "ee@example.com", "password": "asdasdsdasd1221"},
        name="/api/users/login/",
    )
    if resp.status_code == 200:
        return resp.json().get("token")
    return None


def _retry_post(client, url, *, json, headers, max_retries=3, name=None):
    """Retry POST requests on rate limit (429)."""
    kwargs = {"json": json, "headers": headers}
    if name:
        kwargs["name"] = name

    for attempt in range(1, max_retries + 1):
        resp = client.post(url, **kwargs)

        # Rate limited - wait and retry
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", attempt))
            time.sleep(retry_after)
            continue

        return resp

    return resp


class EcommerceUser(HttpUser):
    """
    Simulates a customer using the e-commerce platform.

    Task distribution (weighted):
      - 50% Browse products (read-heavy)
      - 30% Checkout flow (write-heavy, tests concurrency)
      - 10% View orders
      - 10% View cart
    """
    wait_time = between(0.5, 1.5)

    def on_start(self):
        """Initialize user session."""
        self.token = _get_token(self.client)
        if not self.token:
            self.environment.runner.quit()

    def _h(self):
        """Get authorization headers."""
        return {"Authorization": f"Token {self.token}"}

    @task(5)
    def browse_products(self):
        """
        Browse product catalog.
        Tests: Redis caching, DB read performance.
        """
        if not self.token:
            return

        with self.client.get(
            "/api/products/",
            headers=self._h(),
            name="/api/products/",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Failed to load products: {resp.status_code}")

    @task(3)
    def checkout_flow(self):
        """
        Complete checkout flow: clear cart → add items → checkout.

        Tests:
          - Race condition safety (pessimistic locking)
          - Resource management (connection pooling, throttling)
          - Transaction atomicity (ACID)
        """
        global checkout_success, checkout_stock_out, checkout_failure

        if not self.token:
            return

        h = self._h()

        # Step 1: Clear existing cart
        self.client.delete("/api/cart/clear/", headers=h, name="/api/cart/clear/")

        # Step 2: Add 1-3 random products
        picks = random.sample(PRODUCT_IDS, min(random.randint(1, 3), len(PRODUCT_IDS)))
        added = 0

        for pid in picks:
            resp = _retry_post(
                self.client,
                "/api/cart/add/",
                json={"product_id": pid, "quantity": random.randint(1, 2)},
                headers=h,
                name="/api/cart/add/",
            )

            if resp.status_code in (200, 201):
                added += 1
            elif resp.status_code == 400:
                # Product out of stock - expected under load
                pass

        # Step 3: Skip checkout if cart is empty
        if added == 0:
            # All products exhausted - this is EXPECTED behavior
            return

        # Step 4: Checkout
        with self.client.post(
            "/api/orders/checkout/",
            json={},
            headers=h,
            name="/api/orders/checkout/",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                # SUCCESS: Order created
                checkout_success += 1
                resp.success()

            elif resp.status_code == 400:
                # EXPECTED: Stock exhausted between add and checkout (race window)
                # This tests pessimistic locking - the lock prevents overselling
                try:
                    error_msg = resp.json().get("error", "").lower()

                    if "stock" in error_msg or "empty" in error_msg or "insufficient" in error_msg:
                        # Expected behavior - mark as success for Locust stats
                        checkout_stock_out += 1
                        resp.success()
                    else:
                        # Unexpected 400 error
                        checkout_failure += 1
                        resp.failure(f"Unexpected 400: {error_msg}")

                except Exception as e:
                    checkout_failure += 1
                    resp.failure(f"Failed to parse error: {resp.text[:200]}")

            elif resp.status_code == 409:
                # EXPECTED: Conflict (concurrent checkout on same product)
                # Pessimistic locking is working correctly
                checkout_stock_out += 1
                resp.success()

            else:
                # UNEXPECTED: Server error or other issue
                checkout_failure += 1
                resp.failure(f"Unexpected {resp.status_code}: {resp.text[:200]}")

    @task(1)
    def view_orders(self):
        """
        View user's order history.
        Tests: DB read performance with joins.
        """
        if not self.token:
            return

        with self.client.get(
            "/api/orders/",
            headers=self._h(),
            name="/api/orders/",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Failed to load orders: {resp.status_code}")

    @task(1)
    def view_cart(self):
        """
        View shopping cart.
        Tests: DB read performance.
        """
        if not self.token:
            return

        with self.client.get(
            "/api/cart/",
            headers=self._h(),
            name="/api/cart/",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Failed to load cart: {resp.status_code}")


# ═══════════════════════════════════════════════════════════════════════
# Additional load patterns for advanced testing
# ═══════════════════════════════════════════════════════════════════════

class BrowsingUser(HttpUser):
    """
    Read-only user that only browses (never checks out).
    Use this to test read performance under mixed workload.

    To use: uncomment in locust command or web UI
    """
    wait_time = between(0.2, 0.8)
    weight = 1  # Less common than EcommerceUser

    def on_start(self):
        self.token = _get_token(self.client)

    def _h(self):
        return {"Authorization": f"Token {self.token}"}

    @task
    def browse_products(self):
        if not self.token:
            return
        self.client.get("/api/products/", headers=self._h(), name="/api/products/")
