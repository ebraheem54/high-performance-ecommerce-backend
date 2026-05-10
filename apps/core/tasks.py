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

# Number of orders processed per chunk iteration (Requirement 4)
CHUNK_SIZE = 50


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def run_daily_sales_batch_task(self):
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
    """
    from apps.orders.models import Order

    now = timezone.now()
    # Window: midnight yesterday → midnight today (UTC)
    end_dt   = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=1)
    date_label = start_dt.strftime("%Y-%m-%d")

    logger.info(
        "[BATCH] Starting daily sales aggregation for %s (window %s → %s)",
        date_label, start_dt, end_dt,
    )

    # ── Step 1: Collect qualifying order IDs ─────────────────────────────────
    # Fetch IDs only — avoids pulling full ORM objects into memory at once.
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

    # ── Step 2: Process in fixed-size chunks ─────────────────────────────────
    # Synchronization note: each chunk is an independent read-only query.
    # No cross-chunk locking needed — we are only reading committed data.
    for chunk_start in range(0, total_orders, CHUNK_SIZE):
        chunk_ids    = order_ids[chunk_start: chunk_start + CHUNK_SIZE]
        chunk_number = chunk_count + 1

        # Aggregate revenue for this chunk only (one DB round-trip per chunk)
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
            -(-total_orders // CHUNK_SIZE),   # ceiling division
            len(chunk_ids),
            chunk_revenue,
        )

    # ── Step 3: Final summary ─────────────────────────────────────────────────
    logger.info(
        "[BATCH] Daily sales for %s COMPLETE — %s orders, "
        "total_revenue=%.2f, processed in %s chunk(s) of %s",
        date_label, total_orders, total_revenue, chunk_count, CHUNK_SIZE,
    )

    return {
        "date"      : date_label,
        "orders"    : total_orders,
        "revenue"   : float(total_revenue),
        "chunks"    : chunk_count,
        "chunk_size": CHUNK_SIZE,
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
