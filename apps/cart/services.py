"""
Business logic for cart app.

Synchronization Strategy — PESSIMISTIC LOCKING (switched from optimistic):
  Why the change?
  ───────────────
  Cart add is a HIGH CONTENTION endpoint: under load, dozens of requests
  hit the same CartItem row simultaneously. Optimistic locking on a hot
  write path causes cascading conflicts → retries exhaust → RuntimeError.

  Pessimistic locking (SELECT FOR UPDATE) serializes access on the DB side:
    - First request acquires the row lock
    - Subsequent requests WAIT (not fail) until the lock releases
    - No retries needed — every request succeeds in order
    - Correct under high concurrency with minimal code complexity

  Optimistic locking is still appropriate for low-contention paths
  (e.g. product stock pre-checks, reviews).
"""

from apps.cart.models import CartItem
import logging
from django.db import transaction

logger = logging.getLogger(__name__)


def get_cart(user):
    """Return all cart items for a user with product details."""
    return CartItem.objects.select_related("product").filter(user=user)


@transaction.atomic
def add_to_cart(user, product_id: int, quantity: int = 1) -> CartItem:
    """
    Add a product to the cart or increase quantity if already present.

    ── Synchronization Strategy: PESSIMISTIC LOCKING ────────────────────────
    We use SELECT FOR UPDATE on the CartItem row (when it exists) to
    serialize concurrent add-to-cart requests for the same user/product pair.

    Why Pessimistic here?
      Cart add is a hot write path — many requests per second can target
      the same row. Optimistic locking causes conflicts that exhaust retries
      under real load, resulting in 100% failure. Pessimistic locking blocks
      briefly (milliseconds) and guarantees every request succeeds.

    Synchronization points:
      1. Product existence + soft stock check (snapshot read, no lock)
      2. CartItem row locked with SELECT FOR UPDATE → safe update
    """
    from apps.products.models import Product

    # ── Synchronization Point 1: Soft stock check (snapshot read) ─────────────
    # This is NOT a reservation — just a quick guard to reject clearly
    # impossible requests (e.g. quantity > total stock available).
    # The real stock enforcement happens at checkout with pessimistic locking.
    try:
        product = Product.objects.get(id=product_id, is_active=True)
    except Product.DoesNotExist:
        raise ValueError(f"Product {product_id} not found or inactive.")

    if product.stock < quantity:
        raise ValueError(
            f"Only {product.stock} units of '{product.name}' available."
        )

    # ── Synchronization Point 2: Pessimistic lock on CartItem row ─────────────
    # select_for_update() acquires a row-level FOR UPDATE lock.
    # If the row doesn't exist yet, we create it safely inside the transaction.
    # Concurrent requests for the same user/product will wait here, not fail.
    try:
        item = CartItem.objects.select_for_update().get(
            user=user, product_id=product_id
        )
        # Row exists and is locked — safe to update quantity
        item.quantity += quantity
        item.save(update_fields=["quantity", "updated_at"])
        logger.info(
            "Cart item updated (pessimistic): user=%s product=%s new_qty=%s",
            user.id, product_id, item.quantity,
        )
    except CartItem.DoesNotExist:
        # First time this product is added to the cart — create new row
        item = CartItem.objects.create(
            user=user,
            product_id=product_id,
            quantity=quantity,
            version=0,
        )
        logger.info(
            "Cart item created: user=%s product=%s qty=%s",
            user.id, product_id, quantity,
        )

    return item


def remove_from_cart(user, product_id: int) -> bool:
    """Remove a product from the cart. Returns True if deleted."""
    deleted, _ = CartItem.objects.filter(user=user, product_id=product_id).delete()
    return deleted > 0


def clear_cart(user) -> int:
    """Remove all items from the user's cart. Returns count deleted."""
    deleted, _ = CartItem.objects.filter(user=user).delete()
    return deleted


@transaction.atomic
def update_cart_item_quantity(user, product_id: int, quantity: int) -> CartItem:
    """
    Set exact quantity for a cart item using Pessimistic Locking.
    Synchronization point: SELECT FOR UPDATE → prevents concurrent overwrites.
    """
    if quantity <= 0:
        remove_from_cart(user, product_id)
        return None

    try:
        item = CartItem.objects.select_for_update().get(
            user=user, product_id=product_id
        )
    except CartItem.DoesNotExist:
        raise ValueError("Cart item not found.")

    item.quantity = quantity
    item.save(update_fields=["quantity", "updated_at"])
    logger.info(
        "Cart quantity set (pessimistic): user=%s product=%s qty=%s",
        user.id, product_id, quantity,
    )
    return item
    