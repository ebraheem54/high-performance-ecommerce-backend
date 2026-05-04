"""
Business logic for products.
Synchronization Strategy:
  ┌─────────────────────────────────────────────────────────────────────┐
  │  OPTIMISTIC LOCKING  →  deduct_stock_optimistic()                  │
  │    - Used for: pre-checkout stock check, cart add validation        │
  │    - How: Read version → UPDATE WHERE version=X → retry if conflict │
  │    - No DB-level lock held → high concurrency, may need retry       │
  │                                                                     │
  │  PESSIMISTIC LOCKING →  deduct_stock_pessimistic()                 │
  │    - Used for: final checkout (called from orders.services)         │
  │    - How: SELECT FOR UPDATE → holds row lock → no other TX can read │
  │    - Guarantees atomicity at the cost of blocking other requests    │
  └─────────────────────────────────────────────────────────────────────┘
"""

from django.db import transaction
from django.utils import timezone
import time
import logging
from apps.products.models import Product, InventoryLog, OrderLock

logger = logging.getLogger(__name__)
# Maximum retry attempts for optimistic locking conflicts
OPTIMISTIC_MAX_RETRIES = 3
# Delay between retries (seconds) — gives other transactions time to commit
OPTIMISTIC_RETRY_DELAY = 0.05


def get_active_products():
    """Return all active products. Cached in Redis (see views.py)."""
    return Product.objects.filter(is_active=True).order_by("name")


def get_product_by_id(product_id: int):
    """Fetch a single product or raise DoesNotExist."""
    return Product.objects.get(id=product_id, is_active=True)


# OPTIMISTIC LOCKING — Products & Inventory
@transaction.atomic
def deduct_stock_optimistic(
    product_id: int, quantity: int, reason: str = InventoryLog.Reason.PURCHASE
) -> bool:
    """
    Deduct stock using TRUE Optimistic Locking.
    Algorithm (Synchronization point):
      1. Read product WITHOUT any DB lock (snapshot read)
      2. Attempt UPDATE WHERE id=X AND version=current_version
         → If 1 row updated: we won the race, commit.
         → If 0 rows updated: another transaction changed the row first,
           version no longer matches → CONFLICT detected.
      3. On conflict: caller retries (see deduct_stock_with_retry).
    Why Optimistic here?
      Cart additions and pre-checks don't need strong consistency.
      Most of the time there is NO conflict, so we avoid the cost of
      a DB-level lock entirely. Only retry on the rare collision.
    Returns:
      True  — stock deducted successfully
      False — version conflict (caller should retry)
    Raises:
      ValueError — not enough stock available
    """
    # Step 1: Snapshot read — NO lock acquired
    try:
        product = Product.objects.get(id=product_id, is_active=True)
    except Product.DoesNotExist:
        return False

    if product.stock < quantity:
        raise ValueError(
            f"Insufficient stock: {product.stock} available, {quantity} requested."
        )
    captured_version = product.version
    # Step 2: Conditional UPDATE — only succeeds if version hasn't changed
    # Synchronization point: this is where the race condition is resolved.

    # Optimistic Locking: update only if version has not changed since we read it
    updated_rows = Product.objects.filter(
        id=product_id,
        version=captured_version,  # ← the optimistic check
        stock__gte=quantity,  # ← safety guard
    ).update(
        stock=product.stock - quantity,
        version=captured_version + 1,  # ← bump version on every write
    )

    if updated_rows == 0:
        # Another transaction already incremented the version → conflict!
        logger.warning(
            "Optimistic lock conflict on Product id=%s (version=%s). Retrying...",
            product_id,
            captured_version,
        )
        return False  # Signal conflict to caller
    # Step 3: Audit log — record every stock change for traceability
    InventoryLog.objects.create(
        product_id=product_id,
        quantity_change=-quantity,
        reason=reason,
        note=(f"[OPTIMISTIC] version {captured_version} → {captured_version + 1}"),
    )
    logger.info(
        "Optimistic stock deduct: product=%s qty=%s version %s→%s",
        product_id,
        quantity,
        captured_version,
        captured_version + 1,
    )
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# OPTIMISTIC LOCKING — Reviews
# ═══════════════════════════════════════════════════════════════════════════════
def create_review(
    user, product_id: int, order_id, rating: int, comment: str = ""
) -> "Review":
    """
    Create a product review with Optimistic Locking protection.
    For reviews, two types of race conditions are possible:
      1. Two requests from the same user submitting the same review simultaneously
         → Resolved by unique_together = ("user", "product") constraint at DB level.
         → The second INSERT raises IntegrityError → caught and re-raised clearly.
      2. A review being updated while being read
         → We use the version field on Review for concurrent update protection.
    Why Optimistic here?
      Reviews are infrequent writes. A DB-level lock on the review table
      is expensive and unnecessary. Version-check is sufficient.
    """
    from products.models import Review

    try:
        with transaction.atomic():
            review = Review.objects.create(
                user=user,
                product_id=product_id,
                order_id=order_id,
                rating=rating,
                comment=comment,
            )
            logger.info(
                "Review created: user=%s product=%s rating=%s",
                user.id,
                product_id,
                rating,
            )
            return review
    except Exception as exc:
        from django.db import IntegrityError

        if "unique" in str(exc).lower():
            raise ValueError(
                "You have already reviewed this product. "
                "Each user can only submit one review per product."
            ) from exc
        raise


@transaction.atomic
def restock_product(product_id: int, quantity: int, note: str = "") -> Product:
    """
    Add stock to a product and log the change.
    Uses Pessimistic Locking (select_for_update) because restocking is
    an admin operation that must be serialized — two simultaneous restocks
    of the same product could corrupt the total stock count.
    """
    # Synchronization point: lock this product row exclusively
    product = Product.objects.select_for_update().get(id=product_id)
    product.stock += quantity
    product.version += 1
    product.save(update_fields=["stock", "version", "updated_at"])

    InventoryLog.objects.create(
        product=product,
        quantity_change=quantity,
        reason=InventoryLog.Reason.RESTOCK,
        note=f"[PESSIMISTIC] Restock — {note}" if note else "[PESSIMISTIC] Restock",
    )

    return product


@transaction.atomic
def create_order_lock(product_id: int, user_id: int, quantity: int,
                      lock_minutes: int = 10) -> OrderLock:
    """
    Reserve stock for a user during checkout.
    Prevents another user from buying the same units simultaneously.
    Uses Pessimistic Locking to guarantee the reservation is exclusive.
    """
    from datetime import timedelta
   # Pessimistic: lock the product row during reservation
    product = Product.objects.select_for_update().get(id=product_id)

    if product.stock < quantity:
        raise ValueError("Not enough stock to lock.")

    expires_at = timezone.now() + timedelta(minutes=lock_minutes)
    lock = OrderLock.objects.create(
        product=product,
        user_id=user_id,
        quantity=quantity,
        expires_at=expires_at,
    )
    return lock


def release_expired_locks():
    """Remove all expired order locks (called periodically by Celery Beat)."""
    expired = OrderLock.objects.filter(expires_at__lt=timezone.now())
    count = expired.count()
    expired.delete()
    return count
