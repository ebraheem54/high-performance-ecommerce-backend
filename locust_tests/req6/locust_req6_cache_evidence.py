"""
Requirement 6 — Locust runner with Redis cache evidence.

Use this file when presenting to the instructor.
It runs the same Req6 Locust user from locust_req6.py, then prints Redis cache evidence
inside the Locust terminal report so no separate redis-cli screenshots are required.

Run AFTER with UI:
  docker compose run --rm -p 8089:8089 -e LOCUST_MODE=req6_after locust -f locust_req6_cache_evidence.py --host http://nginx:80
"""

from __future__ import annotations

import os
from typing import Iterable

from locust import events

os.environ.setdefault("LOCUST_MODE", "req6_after")

import locust_req6 as base
from locust_req6 import Req6CacheUser


def _redis_exists(key: str) -> tuple[bool, str]:
    """Check both raw and Django-versioned Redis cache keys."""
    raw_response = base._redis_command("EXISTS", key)
    if raw_response and raw_response.startswith(b":1"):
        return True, key

    versioned_key = f":1:{key}"
    versioned_response = base._redis_command("EXISTS", versioned_key)
    if versioned_response and versioned_response.startswith(b":1"):
        return True, versioned_key

    return False, versioned_key


def _print_cache_check(label: str, key: str) -> None:
    exists, redis_key = _redis_exists(key)
    status = "HIT" if exists else "MISS"
    print(f"  {label:<22} {status:<4} key={redis_key}")


def _first_product_id(product_ids: Iterable[int]) -> int | None:
    for product_id in product_ids:
        return int(product_id)
    return None


@events.test_stop.add_listener
def print_req6_cache_evidence(environment, **kwargs):
    if base.LOCUST_MODE != "req6_after":
        return

    print("\n" + "═" * 72)
    print("  REQ 6 AFTER — REDIS CACHE EVIDENCE FROM LOCUST")
    print("═" * 72)

    _print_cache_check("Product list", base.PRODUCT_LIST_CACHE_KEY)
    _print_cache_check("Top selling", base.TOP_SELLING_CACHE_KEY)

    product_id = _first_product_id(base.ALL_PRODUCT_IDS)
    if product_id is None:
        print("  Product detail        SKIP key=no_product_ids_loaded")
        print("  Rating summary        SKIP key=no_product_ids_loaded")
    else:
        _print_cache_check(
            "Product detail",
            f"{base.PRODUCT_DETAIL_CACHE_PREFIX}:{product_id}:customer",
        )
        _print_cache_check(
            "Rating summary",
            f"{base.RATING_SUMMARY_CACHE_PREFIX}:{product_id}",
        )

    print("\nEvidence meaning:")
    print("  HIT  = Redis contains the Django cache key after the AFTER benchmark.")
    print("  MISS = The key was not found, usually because TTL expired or the endpoint was not hit.")
    print("═" * 72 + "\n")
