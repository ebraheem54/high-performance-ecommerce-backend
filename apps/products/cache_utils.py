
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, TypeVar

from django.core.cache import cache

T = TypeVar("T")

PRODUCT_CACHE_TTL = 60 * 5  # 5 minutes
PRODUCT_LIST_CACHE_KEY = "product_list"
TOP_SELLING_PRODUCTS_CACHE_KEY = "top_selling_products"
PRODUCT_DETAIL_CACHE_KEY_PREFIX = "product_detail"
PRODUCT_RATING_SUMMARY_CACHE_KEY_PREFIX = "product_rating_summary"
PRODUCT_MANUAL_INVALIDATION_FIELDS = {"name", "description", "price", "is_active"}
REQ6_BYPASS_CACHE_HEADER = "X-Req6-Bypass-Cache"
REQ7_PRODUCT_LIST_LOCK_KEY = "req7:lock:product_list"
REQ7_TOP_SELLING_LOCK_KEY = "req7:lock:top_selling_products"
REQ7_LOCK_TTL_MS = 5_000
REQ7_WAIT_TIMEOUT_MS = 2_000
REQ7_RETRY_DELAY_MS = 50

_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def should_bypass_cache(request: Any) -> bool:
    """Return True when Req6 BEFORE benchmark wants to bypass Redis cache."""
    return request.headers.get(REQ6_BYPASS_CACHE_HEADER) == "1"


def product_detail_cache_key(product_id: int, is_staff: bool = False) -> str:
    role = "admin" if is_staff else "customer"
    return f"{PRODUCT_DETAIL_CACHE_KEY_PREFIX}:{product_id}:{role}"


def product_rating_summary_cache_key(product_id: int) -> str:
    return f"{PRODUCT_RATING_SUMMARY_CACHE_KEY_PREFIX}:{product_id}"


def touches_manual_cached_fields(data: Any) -> bool:
    """Return True when an admin update touches manually cached public fields."""
    return any(field in data for field in PRODUCT_MANUAL_INVALIDATION_FIELDS)


def get_cached(cache_key: str) -> Any:
    """Read a value from Redis cache."""
    return cache.get(cache_key)


def set_cached(cache_key: str, data: Any, ttl: int = PRODUCT_CACHE_TTL) -> None:
    """Store a value in Redis cache using TTL."""
    cache.set(cache_key, data, ttl)


def get_or_set_cache(cache_key: str, callback: Callable[[], T], ttl: int = PRODUCT_CACHE_TTL) -> T:
    """
    Manual cache-aside helper without locks.

    Flow:
      1. Read from Redis.
      2. On miss, execute callback to read from the database.
      3. Store the result in Redis with TTL.
      4. Return the result.
    """
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    data = callback()
    cache.set(cache_key, data, ttl)
    return data


def acquire_distributed_lock(lock_key: str, lock_value: str, ttl_ms: int) -> bool:
    """
    Acquire a Redis distributed lock with an ownership token and expiration.

    SET key value NX PX ttl_ms ensures only one app container can rebuild the
    same cache key, and the TTL prevents a stuck lock if the owner crashes.
    """
    redis_client = cache.client.get_client(write=True)
    return bool(redis_client.set(lock_key, lock_value, nx=True, px=ttl_ms))


def release_distributed_lock(lock_key: str, lock_value: str) -> bool:
    """
    Release a Redis distributed lock only if this request still owns it.

    The compare-and-delete Lua script prevents an old request from deleting a
    newer request's lock after the original TTL has expired.
    """
    redis_client = cache.client.get_client(write=True)
    return bool(redis_client.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, lock_value))


def get_or_set_cache_with_distributed_lock(
    cache_key: str,
    callback: Callable[[], T],
    *,
    ttl: int = PRODUCT_CACHE_TTL,
    lock_key: str,
    lock_ttl_ms: int = REQ7_LOCK_TTL_MS,
    wait_timeout_ms: int = REQ7_WAIT_TIMEOUT_MS,
    retry_delay_ms: int = REQ7_RETRY_DELAY_MS,
) -> tuple[T, dict[str, Any]]:
    """
    Cache-aside with a Redis distributed lock for Req 7.

    Only the lock owner may rebuild and write the cache. Other concurrent
    requests wait briefly and re-read Redis. If the wait budget is exhausted,
    they may fall back to a direct DB read but do not write that value to Redis.
    """
    metadata: dict[str, Any] = {
        "cache_key": cache_key,
        "lock_key": lock_key,
        "cache_hit": False,
        "lock_acquired": False,
        "db_query_executed": False,
        "served_from_cache": False,
        "served_after_wait": False,
        "fallback_used": False,
        "cache_status": "MISS",
        "waited_ms": 0,
        "lock_ttl_ms": lock_ttl_ms,
        "wait_timeout_ms": wait_timeout_ms,
        "retry_delay_ms": retry_delay_ms,
    }

    cached = cache.get(cache_key)
    if cached is not None:
        metadata["cache_hit"] = True
        metadata["served_from_cache"] = True
        metadata["cache_status"] = "HIT"
        return cached, metadata

    lock_value = uuid.uuid4().hex
    if acquire_distributed_lock(lock_key, lock_value, lock_ttl_ms):
        metadata["lock_acquired"] = True
        try:
            cached = cache.get(cache_key)
            if cached is not None:
                metadata["cache_hit"] = True
                metadata["served_from_cache"] = True
                metadata["cache_status"] = "HIT_AFTER_DOUBLE_CHECK"
                return cached, metadata

            data = callback()
            metadata["db_query_executed"] = True
            metadata["cache_status"] = "MISS_LOCK_OWNER_REBUILD"
            cache.set(cache_key, data, ttl)
            return data, metadata
        finally:
            release_distributed_lock(lock_key, lock_value)

    started = time.monotonic()
    deadline = started + (wait_timeout_ms / 1000)
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        time.sleep((retry_delay_ms * attempt) / 1000)
        metadata["waited_ms"] = int((time.monotonic() - started) * 1000)

        cached = cache.get(cache_key)
        if cached is not None:
            metadata["cache_hit"] = True
            metadata["served_from_cache"] = True
            metadata["served_after_wait"] = True
            metadata["cache_status"] = "WAITED_THEN_HIT_NO_DB"
            return cached, metadata

    data = callback()
    metadata["db_query_executed"] = True
    metadata["fallback_used"] = True
    metadata["cache_status"] = "MISS_FALLBACK_DB"
    metadata["waited_ms"] = int((time.monotonic() - started) * 1000)
    return data, metadata


def invalidate_product_read_caches(product_id: int | None = None, *, include_rating: bool = False) -> None:
    """
    Invalidate cached read endpoints affected by product display changes.

    Stock is intentionally not cached in public read endpoints, so stock-only
    changes such as restock do not need to invalidate product list/detail cache.
    """
    cache.delete(PRODUCT_LIST_CACHE_KEY)
    cache.delete(TOP_SELLING_PRODUCTS_CACHE_KEY)

    if product_id is not None:
        cache.delete(product_detail_cache_key(product_id, is_staff=False))
        cache.delete(product_detail_cache_key(product_id, is_staff=True))

        if include_rating:
            cache.delete(product_rating_summary_cache_key(product_id))


def invalidate_rating_summary_cache(product_id: int) -> None:
    """Invalidate only the rating summary cache for a product."""
    cache.delete(product_rating_summary_cache_key(product_id))
