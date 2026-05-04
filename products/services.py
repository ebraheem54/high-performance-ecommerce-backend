"""
Business logic for products.
Handles stock management with Optimistic Locking to prevent race conditions.
"""

from django.db import transaction
from django.utils import timezone

from products.models import Product, InventoryLog, OrderLock


def get_active_products():
    """Return all active products. Cached in Redis (see views.py)."""
    return Product.objects.filter(is_active=True).order_by("name")


def get_product_by_id(product_id: int):
    """Fetch a single product or raise DoesNotExist."""
    return Product.objects.get(id=product_id, is_active=True)


@transaction.atomic
def deduct_stock_optimistic(product_id: int, quantity: int, reason: str = InventoryLog.Reason.PURCHASE) -> bool:
    """
    Deduct stock using Optimistic Locking.
    Reads current version, applies update only if version unchanged.
    Returns True on success, False on conflict (caller should retry).

    Synchronization point: SELECT ... WHERE id=X AND version=Y → UPDATE
    """
    try:
        product = Product.objects.select_for_update().get(id=product_id)
    except Product.DoesNotExist:
        return False

    if product.stock < quantity:
        raise ValueError(f"Insufficient stock: {product.stock} available, {quantity} requested.")

    current_version = product.version

    # Optimistic Locking: update only if version has not changed since we read it
    updated = Product.objects.filter(
        id=product_id,
        version=current_version,
    ).update(
        stock=product.stock - quantity,
        version=current_version + 1,
    )

    if updated == 0:
        # Another transaction changed the row — conflict detected
        return False

    InventoryLog.objects.create(
        product=product,
        quantity_change=-quantity,
        reason=reason,
        note=f"Optimistic lock version {current_version} → {current_version + 1}",
    )
    return True


@transaction.atomic
def restock_product(product_id: int, quantity: int, note: str = "") -> Product:
    """Add stock to a product and log the change."""
    product = Product.objects.select_for_update().get(id=product_id)
    product.stock += quantity
    product.version += 1
    product.save(update_fields=["stock", "version", "updated_at"])

    InventoryLog.objects.create(
        product=product,
        quantity_change=quantity,
        reason=InventoryLog.Reason.RESTOCK,
        note=note,
    )
    return product


@transaction.atomic
def create_order_lock(product_id: int, user_id: int, quantity: int, lock_minutes: int = 10) -> OrderLock:
    """
    Reserve stock for a user during checkout.
    Prevents another user from buying the same units.
    """
    from datetime import timedelta
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
