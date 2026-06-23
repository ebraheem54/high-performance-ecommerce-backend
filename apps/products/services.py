"""Business logic for products, stock updates, reservations, and reviews."""

from django.db import transaction
from django.utils import timezone
import time
import logging
from apps.products.models import Product, InventoryLog, OrderLock
from apps.core.logging_utils import log_service_call, log_user_event

logger = logging.getLogger(__name__)

# Maximum retry attempts for optimistic locking conflicts.
OPTIMISTIC_MAX_RETRIES = 5
# Delay between retries so competing transactions can commit.
OPTIMISTIC_RETRY_DELAY = 0.05


def get_active_products():
    """Return all active products. Cached in Redis (see views.py)."""
    return Product.objects.filter(is_active=True).order_by("name")


def get_product_by_id(product_id: int):
    """Fetch a single product or raise DoesNotExist."""
    return Product.objects.get(id=product_id, is_active=True)


# Optimistic locking for product stock updates.
@transaction.atomic
@log_service_call(
    "product.stock.deduct_optimistic",
    context_builder=lambda args: {
        "product_id": args["product_id"],
        "quantity": args["quantity"],
        "reason": args["reason"],
    },
)
def deduct_stock_optimistic(
    product_id: int, quantity: int, reason: str = InventoryLog.Reason.PURCHASE
) -> bool:

    # Snapshot read without acquiring a database lock.
    try:
        product = Product.objects.get(id=product_id, is_active=True)
    except Product.DoesNotExist:
        return False

    if product.stock < quantity:
        raise ValueError(
            f"Insufficient stock: {product.stock} available, {quantity} requested."
        )
    captured_version = product.version

    # Synchronization point: this is where the race condition is resolved.
    # The update only succeeds if the version has not changed since the read.
    updated_rows = Product.objects.filter(
        id=product_id,
        version=captured_version,   # ← the optimistic check
        stock__gte=quantity,        # ← safety guard
    ).update(
        stock=product.stock - quantity,
        version=captured_version + 1,   # ← bump version on every write
    )

    if updated_rows == 0:
        # Another transaction already incremented the version → conflict!
        logger.warning(
            "Optimistic lock conflict on Product id=%s (version=%s). Retrying...",
            product_id,
            captured_version,
        )
        return False  # Signal conflict to caller

    # Record every stock change for traceability.
    InventoryLog.objects.create(
        product_id=product_id,
        quantity_change=-quantity,
        reason=reason,
        note=(f"[OPTIMISTIC] version {captured_version} → {captured_version + 1}"),
    )
    return True


@log_service_call(
    "product.stock.deduct_retry",
    context_builder=lambda args: {
        "product_id": args["product_id"],
        "quantity": args["quantity"],
        "reason": args["reason"],
    },
)
def deduct_stock_with_retry(
    product_id: int,
    quantity: int,
    reason: str = InventoryLog.Reason.PURCHASE,
    max_retries: int = OPTIMISTIC_MAX_RETRIES,
    retry_delay: float = OPTIMISTIC_RETRY_DELAY,
) -> bool:

    for attempt in range(1, max_retries + 1):
        try:
            success = deduct_stock_optimistic(product_id, quantity, reason)
            if success:
                return True
            # Version conflict — back off and retry
            logger.warning(
                "Optimistic retry %s/%s for product=%s qty=%s",
                attempt, max_retries, product_id, quantity,
            )
            time.sleep(retry_delay * attempt)  # exponential back-off
        except ValueError:
            raise  # not enough stock — no point retrying
    raise RuntimeError(
        f"Could not deduct stock for product={product_id} after "
        f"{max_retries} optimistic retries."
    )


@transaction.atomic
@log_service_call(
    "product.stock.deduct_pessimistic",
    context_builder=lambda args: {"product_id": args["product"].id, "quantity": args["quantity"]},
)
def deduct_stock_pessimistic(product: Product, quantity: int) -> None:

    if product.stock < quantity:
        raise ValueError(
            f"Insufficient stock for '{product.name}': "
            f"{product.stock} available, {quantity} requested."
        )
    product.stock -= quantity
    product.version += 1
    product.save(update_fields=["stock", "version", "updated_at"])

    InventoryLog.objects.create(
        product=product,
        quantity_change=-quantity,
        reason=InventoryLog.Reason.PURCHASE,
        note=f"[PESSIMISTIC] checkout deduction version→{product.version}",
    )

# Optimistic locking for reviews.
@log_service_call(
    "review.create",
    context_builder=lambda args: {
        "user_id": args["user"].id,
        "product_id": args["product_id"],
        "order_id": args["order_id"],
        "rating": args["rating"],
    },
    result_builder=lambda result, args: {"review_id": result.id},
)
def create_review(
    user, product_id: int, order_id, rating: int, comment: str = ""
) -> "Review":

    from django.db import IntegrityError
    from apps.products.models import Review

    try:
        with transaction.atomic():
            review = Review.objects.create(
                user=user,
                product_id=product_id,
                order_id=order_id,
                rating=rating,
                comment=comment,
            )
            log_user_event(
                user.id,
                "review.create",
                product_id=product_id,
                order_id=order_id,
                rating=rating,
                result="created",
            )
            return review
    except IntegrityError as exc:
        raise ValueError(
            "You have already reviewed this product. "
            "Each user can only submit one review per product."
        ) from exc


@transaction.atomic
@log_service_call(
    "product.restock",
    context_builder=lambda args: {"product_id": args["product_id"], "quantity": args["quantity"]},
    result_builder=lambda result, args: {"new_stock": result.stock},
)
def restock_product(product_id: int, quantity: int, note: str = "") -> Product:
   
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
@log_service_call(
    "product.reserve",
    context_builder=lambda args: {
        "product_id": args["product_id"],
        "user_id": args["user_id"],
        "quantity": args["quantity"],
    },
    result_builder=lambda result, args: {"lock_id": result.id},
)
def create_order_lock(product_id: int, user_id: int, quantity: int,
                      lock_minutes: int = 10) -> OrderLock:

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
    log_user_event(
        user_id,
        "product.reserve",
        product_id=product_id,
        quantity=quantity,
        lock_id=lock.id,
        lock="pessimistic",
        result="reserved",
    )
    return lock


@log_service_call("product.release_expired_locks")
def release_expired_locks():
    """Remove all expired order locks (called periodically by Celery Beat)."""
    expired = OrderLock.objects.filter(expires_at__lt=timezone.now())
    count = expired.count()
    expired.delete()
    return count
