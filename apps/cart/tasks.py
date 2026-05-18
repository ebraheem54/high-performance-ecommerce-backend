"""
Async Celery tasks for cart app — Batch Processing (Requirement 4).
"""

from celery import shared_task
import logging

logger = logging.getLogger(__name__)

# Process deletions in chunks to avoid holding a large table lock
CLEANUP_CHUNK_SIZE = 100


@shared_task
def cleanup_abandoned_carts(days_old: int = 30):
    """
    Batch Processing (Requirement 4):
    Remove cart items that haven't been updated in X days.

    Runs as a scheduled Celery Beat job (every Sunday midnight).

    Why chunked?
      A single DELETE of thousands of rows holds a large lock and
      blocks concurrent reads/writes on the cart table.  Processing
      in chunks of CLEANUP_CHUNK_SIZE rows releases the lock between
      iterations, keeping the system responsive under load.

    Asynchronous Processing (Requirement 3):
      Runs entirely inside a Celery worker — no HTTP request is
      waiting for this task to finish.
    """
    from django.utils import timezone
    from datetime import timedelta
    from apps.cart.models import CartItem

    cutoff = timezone.now() - timedelta(days=days_old)

    # ── Step 1: Collect stale IDs (lightweight — IDs only) ───────────────────
    stale_ids = list(
        CartItem.objects
        .filter(updated_at__lt=cutoff)
        .values_list("id", flat=True)
        .order_by("id")
    )

    total_deleted = 0
    chunk_count   = 0

    # ── Step 2: Delete in fixed-size chunks ───────────────────────────────────
    for chunk_start in range(0, len(stale_ids), CLEANUP_CHUNK_SIZE):
        chunk_ids = stale_ids[chunk_start: chunk_start + CLEANUP_CHUNK_SIZE]
        deleted, _ = CartItem.objects.filter(id__in=chunk_ids).delete()
        total_deleted += deleted
        chunk_count   += 1
        logger.info(
            "[BATCH] cleanup_abandoned_carts chunk %s: deleted %s cart items",
            chunk_count, deleted,
        )

    logger.info(
        "[BATCH] cleanup_abandoned_carts complete: %s items removed in %s chunk(s)",
        total_deleted, chunk_count,
    )
    return f"Deleted {total_deleted} abandoned cart items older than {days_old} days."
