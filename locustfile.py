"""
Locust load test for the High-Performance E-Commerce Backend.

Tests two non-functional requirements:
  1. Race Condition safety  — concurrent checkouts on the same products
  2. Resource Management    — throttle, connection pool, response times under load

Run:
  locust -f locustfile.py --host http://localhost:8000
Then open http://localhost:8089 to start the simulation.

Performance notes:
  - Login bottleneck: Django dev server is single-threaded; concurrent logins
    queue up and each password hash (~100ms) blocks the thread. This file
    solves it by pre-building a shared token pool in on_test_start so users
    never login during the actual test.
  - For production-accurate results run gunicorn with multiple workers:
      gunicorn --workers=4 --bind=0.0.0.0:8000 config.wsgi:application
"""

import random
import time
import threading
import requests as _requests
from locust import HttpUser, task, between, events

PRODUCT_IDS: list = []
TOKEN_POOL:   list = []
_pool_lock = threading.Lock()


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    global PRODUCT_IDS, TOKEN_POOL
    base = environment.host.rstrip("/")

    print("[setup] Pre-authenticating test user…")
    try:
        login_resp = _requests.post(
            f"{base}/api/users/login/",
            json={"email": "ee@example.com", "password": "asdasdsdasd1221"},
            timeout=60,
        )
        if login_resp.status_code != 200:
            print(f"[setup] Login failed ({login_resp.status_code}): {login_resp.text[:200]}")
            return

        token = login_resp.json().get("token")
        TOKEN_POOL.append(token)
        print(f"[setup] Token acquired.")

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
                PRODUCT_IDS = [p["id"] for p in results if p.get("stock", 1) > 0]
                print(f"[setup] Loaded {len(PRODUCT_IDS)} in-stock products.")

    except Exception as exc:
        print(f"[setup] Error: {exc}")

    if not PRODUCT_IDS:
        PRODUCT_IDS = list(range(1, 51))
        print("[setup] Fallback: using product IDs 1–50.")

    if not TOKEN_POOL:
        print("[setup] WARNING: No token obtained. Users will login individually.")


def _get_token(client) -> str | None:
    if TOKEN_POOL:
        return TOKEN_POOL[0]
    resp = client.post(
        "/api/users/login/",
        json={"email": "ee@example.com", "password": "asdasdsdasd1221"},
        name="/api/users/login/",
    )
    if resp.status_code == 200:
        return resp.json().get("token")
    return None


def _retry_post(client, url, *, json, headers, max_retries=3, name=None):
    kwargs = {"json": json, "headers": headers}
    if name:
        kwargs["name"] = name
    for attempt in range(1, max_retries + 1):
        resp = client.post(url, **kwargs)
        if resp.status_code != 429:
            return resp
        retry_after = int(resp.headers.get("Retry-After", attempt))
        time.sleep(retry_after)
    return resp


class EcommerceUser(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self):
        self.token = _get_token(self.client)

    def _h(self):
        return {"Authorization": f"Token {self.token}"}

    @task(5)
    def browse_products(self):
        if not self.token:
            return
        self.client.get("/api/products/", headers=self._h(), name="/api/products/")

    @task(3)
    def checkout_flow(self):
        if not self.token:
            return
        h = self._h()

        self.client.delete("/api/cart/clear/", headers=h, name="/api/cart/clear/")

        picks = random.sample(PRODUCT_IDS, min(random.randint(1, 3), len(PRODUCT_IDS)))
        added = 0
        for pid in picks:
            resp = _retry_post(
                self.client, "/api/cart/add/",
                json={"product_id": pid, "quantity": random.randint(1, 2)},
                headers=h, name="/api/cart/add/",
            )
            if resp.status_code in (200, 201):
                added += 1

        if added == 0:
            return

        with self.client.post(
            "/api/orders/checkout/",
            json={},
            headers=h,
            name="/api/orders/checkout/",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                resp.success()
            elif resp.status_code == 400:
                resp.success()   # stock-out = expected correct behaviour
            else:
                resp.failure(f"Unexpected {resp.status_code}: {resp.text[:200]}")

    @task(1)
    def view_orders(self):
        if not self.token:
            return
        self.client.get("/api/orders/", headers=self._h(), name="/api/orders/")

    @task(1)
    def view_cart(self):
        if not self.token:
            return
        self.client.get("/api/cart/", headers=self._h(), name="/api/cart/")
