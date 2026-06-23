"""
Core background tasks — Batch Processing (Requirement 4).

run_daily_sales_batch_task:
  Aggregates all CONFIRMED/PROCESSING/SHIPPED/DELIVERED orders from the
  previous calendar day and processes them in fixed-size CHUNKS.

  Why chunks?
    Loading thousands of rows at once into memory causes:
      - High RAM usage (potential OOM under heavy load)
      - Long DB query hold-time (blocks other transactions)
    Processing in chunks of CHUNK_SIZE rows keeps memory flat and
    gives the DB connection back between iterations, allowing other
    requests to proceed — a core parallel-programming principle.

  Scheduled via Celery Beat: every night at 01:00 (see settings.py).
"""

import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)

CHUNK_SIZE = 50


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def run_daily_sales_batch_task(self, demo_chunk_size: int = None):
    """
    Batch Processing (Requirement 4):
    Aggregate yesterday's sales in chunks of CHUNK_SIZE.

    Algorithm:
      1. Determine the date window: [yesterday 00:00, today 00:00)
      2. Fetch matching order IDs (lightweight — IDs only, no heavy JOIN)
      3. Slice into chunks of CHUNK_SIZE and process each chunk:
           - Sum revenue for that chunk
           - Log a DailySalesChunk record
      4. Emit a final summary log entry.

    Asynchronous Processing (Requirement 3 / Requirement 4):
      This entire task runs inside a Celery worker — completely off the
      HTTP request path.  No user is waiting for this to finish.

    demo_chunk_size (optional, DEMO ONLY):
      Override CHUNK_SIZE for a single run without changing the global constant.
      Useful to show "Chunk 1/4" with fewer orders (e.g. demo_chunk_size=10).
      Passing None (default) uses the production CHUNK_SIZE=50.
    """
    from apps.orders.models import Order

    effective_chunk_size = demo_chunk_size if demo_chunk_size else CHUNK_SIZE

    now = timezone.now()
    end_dt   = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=1)
    date_label = start_dt.strftime("%Y-%m-%d")

    logger.info(
        "[BATCH] Starting daily sales aggregation for %s (window %s → %s)",
        date_label, start_dt, end_dt,
    )

    COMPLETED_STATUSES = [
        Order.Status.CONFIRMED,
        Order.Status.PROCESSING,
        Order.Status.SHIPPED,
        Order.Status.DELIVERED,
    ]
    order_ids = list(
        Order.objects
        .filter(
            created_at__gte=start_dt,
            created_at__lt=end_dt,
            status__in=COMPLETED_STATUSES,
        )
        .values_list("id", flat=True)
        .order_by("id")
    )

    total_orders  = len(order_ids)
    total_revenue = 0
    chunk_count   = 0

    if total_orders == 0:
        logger.info("[BATCH] No orders found for %s. Done.", date_label)
        return {"date": date_label, "orders": 0, "revenue": 0, "chunks": 0}

    for chunk_start in range(0, total_orders, effective_chunk_size):
        chunk_ids    = order_ids[chunk_start: chunk_start + effective_chunk_size]
        chunk_number = chunk_count + 1

        from django.db.models import Sum
        chunk_revenue = (
            Order.objects
            .filter(id__in=chunk_ids)
            .aggregate(total=Sum("total_price"))["total"]
            or 0
        )

        total_revenue += chunk_revenue
        chunk_count   += 1

        logger.info(
            "[BATCH] Chunk %s/%s processed: %s orders, revenue=%.2f",
            chunk_number,
            -(-total_orders // effective_chunk_size),
            len(chunk_ids),
            chunk_revenue,
        )

    logger.info(
        "[BATCH] Daily sales for %s COMPLETE — %s orders, "
        "total_revenue=%.2f, processed in %s chunk(s) of %s",
        date_label, total_orders, total_revenue, chunk_count, effective_chunk_size,
    )

    return {
        "date"      : date_label,
        "orders"    : total_orders,
        "revenue"   : float(total_revenue),
        "chunks"    : chunk_count,
        "chunk_size": effective_chunk_size,
    }


@shared_task(bind=True)
def run_daily_sales_batch_naive_task(self, days_back: int = 1):
    """
    ⚠ DEMO ONLY — Naive Batch: no chunking, all orders in one shot.

    Compare with run_daily_sales_batch_task which uses CHUNK_SIZE=50.
    Logs use [BATCH-NAIVE] prefix so they are easy to distinguish.
    """
    import time as _time
    from apps.orders.models import Order

    started = _time.time()
    now     = timezone.now()
    end_dt  = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt   = end_dt - timedelta(days=days_back)
    date_label = start_dt.strftime("%Y-%m-%d")

    logger.warning(
        "[BATCH-NAIVE] ⚠ DEMO ONLY — Starting naive (no-chunk) aggregation "
        "for window %s → %s",
        start_dt, end_dt,
    )

    COMPLETED_STATUSES = [
        Order.Status.CONFIRMED,
        Order.Status.PROCESSING,
        Order.Status.SHIPPED,
        Order.Status.DELIVERED,
    ]

    all_orders = list(
        Order.objects
        .filter(
            created_at__gte=start_dt,
            created_at__lt=end_dt,
            status__in=COMPLETED_STATUSES,
        )
        .order_by("id")
    )

    total_orders = len(all_orders)

    if total_orders == 0:
        elapsed = round(_time.time() - started, 3)
        logger.warning(
            "[BATCH-NAIVE] No orders found for window %s → %s (days_back=%s). "
            "Try: POST /api/core/trigger-batch-naive/ with {\"days_back\": 7}",
            start_dt, end_dt, days_back,
        )
        return {
            "date"     : date_label,
            "orders"   : 0,
            "revenue"  : 0,
            "elapsed_s": elapsed,
            "method"   : "NAIVE — no chunking (BEFORE solution demo)",
            "tip"      : "Pass days_back=7 in request body to widen the window",
        }

    logger.warning(
        "[BATCH-NAIVE] ⚠ Loaded ALL %s order objects into memory at once "
        "— NO CHUNKING (this is the problem we solve with chunks)",
        total_orders,
    )

    total_revenue = 0.0
    for order in all_orders:
        total_revenue += float(order.total_price)

    elapsed = round(_time.time() - started, 3)

    logger.warning(
        "[BATCH-NAIVE] ⚠ DEMO COMPLETE — %s orders processed in ONE SHOT "
        "(no chunks), revenue=%.2f, elapsed=%.3fs  "
        "← compare with [BATCH] Chunk X/Y logs from the real task",
        total_orders, total_revenue, elapsed,
    )

    return {
        "date"        : date_label,
        "orders"      : total_orders,
        "revenue"     : round(total_revenue, 2),
        "elapsed_s"   : elapsed,
        "chunks"      : 1,
        "chunk_size"  : "N/A — all at once",
        "method"      : "NAIVE — no chunking (BEFORE solution demo)",
    }


@shared_task
def cleanup_abandoned_carts():
    """
    Batch Processing (Requirement 4):
    Remove cart items that have not been updated in more than 30 days.
    Runs every Sunday at midnight via Celery Beat (see settings.py).

    Processes deletions in chunks to avoid a single large DELETE
    locking the cart table for an extended period.
    """
    from apps.cart.models import CartItem

    cutoff = timezone.now() - timedelta(days=30)

    # Fetch stale IDs in chunks and delete batch by batch
    stale_ids = list(
        CartItem.objects
        .filter(updated_at__lt=cutoff)
        .values_list("id", flat=True)
        .order_by("id")
    )

    total_deleted = 0
    for chunk_start in range(0, len(stale_ids), CHUNK_SIZE):
        chunk_ids = stale_ids[chunk_start: chunk_start + CHUNK_SIZE]
        deleted, _ = CartItem.objects.filter(id__in=chunk_ids).delete()
        total_deleted += deleted
        logger.info(
            "[BATCH] cleanup_abandoned_carts: deleted %s cart items (chunk)",
            deleted,
        )

    logger.info(
        "[BATCH] cleanup_abandoned_carts complete: %s total items removed.",
        total_deleted,
    )
    return {"deleted": total_deleted}
