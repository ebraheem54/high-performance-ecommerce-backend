"""
Business logic for cart app.
Synchronization Strategy — OPTIMISTIC LOCKING:
  Adding/updating cart items uses optimistic locking on CartItem.version.
  This prevents two concurrent requests (e.g., double-click add) from
  corrupting the cart quantity without the cost of a DB-level lock.
  Algorithm:
    1. Read CartItem (version=N)
    2. UPDATE WHERE id=X AND version=N → set quantity, version=N+1
    3. If 0 rows updated → conflict → retry
"""

from apps.cart.models import CartItem


import logging
from django.db import transaction, IntegrityError

logger = logging.getLogger(__name__)
MAX_RETRIES = 3


def get_cart(user):
    """Return all cart items for a user with product details."""
    return CartItem.objects.select_related("product").filter(user=user)


def add_to_cart(user, product_id: int, quantity: int = 1) -> CartItem:
    """
    Add a product to the cart or increase quantity if already present.
    Optimistic Locking is applied when updating an existing cart item:
      - Read the current version of the CartItem
      - UPDATE WHERE id=X AND version=current_version
      - If conflict (0 rows updated) → retry up to MAX_RETRIES times
    Why Optimistic here?
      A user clicking "Add to Cart" multiple times in quick succession
      could trigger concurrent requests. Optimistic locking ensures the
      quantity is always accurate without blocking other cart operations.
    Synchronization points:
      1. Product stock check — snapshot read, no lock (optimistic)
      2. CartItem update   — version-check UPDATE (optimistic)
    """
    from products.models import Product

    # Snapshot read of product — no lock needed here (optimistic approach)
    try:
        product = Product.objects.get(id=product_id, is_active=True)
    except Product.DoesNotExist:
        raise ValueError(f"Product {product_id} not found or inactive.")
    # Soft stock check — approximate, not a hard reservation
    if product.stock < quantity:
        raise ValueError(f"Only {product.stock} units of '{product.name}' available.")

    # Try to create a new cart item, or get the existing one
    item, created = CartItem.objects.get_or_create(
        user=user,
        product=product,
        defaults={"quantity": quantity, "version": 0},
    )
    if created:
        logger.info(
            "Cart item created: user=%s product=%s qty=%s",
            user.id,
            product_id,
            quantity,
        )
        return item

    # ── Synchronization point: Optimistic Locking on CartItem update ──────────
    # If item already exists, update quantity with optimistic version check
    for attempt in range(1, MAX_RETRIES + 1):
        # Re-read the latest version from DB
        try:
            item = CartItem.objects.get(user=user, product=product)
        except CartItem.DoesNotExist:
            # Edge case: item was deleted between our get_or_create and here
            item = CartItem.objects.create(
                user=user, product=product, quantity=quantity, version=0
            )
            return item
        current_version = item.version
        new_quantity = item.quantity + quantity
        # Conditional UPDATE — only succeeds if version hasn't changed
        updated_rows = CartItem.objects.filter(
            id=item.id,
            version=current_version,  # ← optimistic check
        ).update(
            quantity=new_quantity,
            version=current_version + 1,
        )
        if updated_rows == 1:
            # Success — refresh the item and return it
            item.quantity = new_quantity
            item.version = current_version + 1
            logger.info(
                "Cart item updated (optimistic v%s→v%s): user=%s product=%s qty=%s",
                current_version,
                current_version + 1,
                user.id,
                product_id,
                new_quantity,
            )
            return item
        # Conflict: another request modified the same cart item
        logger.warning(
            "Optimistic conflict on CartItem user=%s product=%s (attempt %s/%s)",
            user.id,
            product_id,
            attempt,
            MAX_RETRIES,
        )
    raise RuntimeError(
        f"Could not update cart after {MAX_RETRIES} attempts due to concurrent modifications."
    )


def remove_from_cart(user, product_id: int) -> bool:
    """Remove a product from the cart. Returns True if deleted."""
    deleted, _ = CartItem.objects.filter(user=user, product_id=product_id).delete()
    return deleted > 0


def clear_cart(user) -> int:
    """Remove all items from the user's cart. Returns count deleted."""
    deleted, _ = CartItem.objects.filter(user=user).delete()
    return deleted


def update_cart_item_quantity(user, product_id: int, quantity: int) -> CartItem:
    """
    Set exact quantity for a cart item using Optimistic Locking.
    Synchronization point: version-check UPDATE to prevent concurrent overwrites.
    """
    if quantity <= 0:
        remove_from_cart(user, product_id)
        return None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            item = CartItem.objects.get(user=user, product_id=product_id)
        except CartItem.DoesNotExist:
            raise ValueError("Cart item not found.")
        current_version = item.version
        # Optimistic update — only if version unchanged
        updated_rows = CartItem.objects.filter(
            id=item.id,
            version=current_version,
        ).update(
            quantity=quantity,
            version=current_version + 1,
        )
        if updated_rows == 1:
            item.quantity = quantity
            item.version = current_version + 1
            return item
        logger.warning(
            "Optimistic conflict on cart quantity update: user=%s product=%s attempt=%s",
            user.id, product_id, attempt,
        )
    raise RuntimeError(
        f"Could not update cart quantity after {MAX_RETRIES} attempts."
    )
