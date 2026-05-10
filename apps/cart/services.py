"""
Business logic for cart app.

Synchronization Strategy:
  ┌─────────────────────────────────────────────────────────────────────┐
  │  add_to_cart       → OPTIMISTIC LOCKING                            │
  │    Why? Cart updates are per-user/product — conflict rate is LOW.   │
  │    Optimistic locking avoids DB-level locks, maximizes throughput.  │
  │    On conflict (version mismatch) the request retries up to N times.│
  │                                                                     │
  │  update_cart_item_quantity → PESSIMISTIC LOCKING                   │
  │    Explicit "set quantity" needs strict serialization — pessimistic  │
  │    guarantees the last writer wins with no ambiguity.               │
  └─────────────────────────────────────────────────────────────────────┘
"""

from apps.cart.models import CartItem
import logging
import time
from django.db import transaction

logger = logging.getLogger(__name__)

# Optimistic locking config for cart item updates
CART_OPTIMISTIC_MAX_RETRIES = 5
CART_OPTIMISTIC_RETRY_DELAY = 0.03   # seconds base; multiplied by attempt number


def get_cart(user):
    """Return all cart items for a user with product details."""
    return CartItem.objects.select_related("product").filter(user=user)


@transaction.atomic
def _try_add_to_cart_optimistic(user, product_id: int, quantity: int):
    """
    Single attempt at an optimistic-lock cart add/update.

    Synchronization point — Optimistic Locking on CartItem:
      1. Read the CartItem row WITHOUT a DB lock (snapshot read).
      2. Issue UPDATE ... WHERE id=X AND version=<captured_version>.
         → 1 row updated  → success (no concurrent write happened).
         → 0 rows updated → conflict (another request bumped version first).
      3. Return False on conflict so the caller can retry.

    For new rows (CartItem.DoesNotExist), we INSERT unconditionally inside
    this atomic block.  A unique_together constraint (user, product) guards
    against duplicate-insert races — if two requests race to create the same
    item, the second gets IntegrityError which is caught by the retry loop.
    """
    from apps.products.models import Product

    # ── Soft stock guard (snapshot read — no lock) ────────────────────────────
    try:
        product = Product.objects.get(id=product_id, is_active=True)
    except Product.DoesNotExist:
        raise ValueError(f"Product {product_id} not found or inactive.")

    if product.stock < quantity:
        raise ValueError(
            f"Only {product.stock} units of '{product.name}' available."
        )

    # ── Attempt optimistic update on existing row ─────────────────────────────
    try:
        # Snapshot read — NO SELECT FOR UPDATE
        item = CartItem.objects.get(user=user, product_id=product_id)
        captured_version = item.version

        # Synchronization point: conditional UPDATE checks version hasn't changed
        updated_rows = CartItem.objects.filter(
            id=item.id,
            version=captured_version,          # ← optimistic check
        ).update(
            quantity=item.quantity + quantity,
            version=captured_version + 1,      # ← bump version on every write
        )

        if updated_rows == 0:
            # Another concurrent request already updated this row → conflict
            logger.warning(
                "Optimistic lock conflict on CartItem user=%s product=%s "
                "(version=%s). Retrying...",
                user.id, product_id, captured_version,
            )
            return None   # Signal: retry

        # Refresh from DB to return accurate data
        item.refresh_from_db()
        logger.info(
            "Cart item updated (optimistic): user=%s product=%s "
            "new_qty=%s version=%s→%s",
            user.id, product_id, item.quantity,
            captured_version, captured_version + 1,
        )
        return item

    except CartItem.DoesNotExist:
        # First time this product is added — create new row
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


def add_to_cart(user, product_id: int, quantity: int = 1) -> CartItem:
    """
    Add a product to the cart or increase quantity if already present.

    Uses OPTIMISTIC LOCKING with automatic retry.

    Why Optimistic here?
      Each user has their own cart rows — the probability of two requests
      from the SAME user for the SAME product at the SAME millisecond is
      very low.  Optimistic locking avoids a DB-level lock entirely and
      gives higher throughput than pessimistic under normal load.
      If conflict occurs (rare), we back off briefly and retry.

    Resource Management (Requirement 2):
      Exponential back-off (retry_delay * attempt) prevents a thundering
      herd of retries from overloading the database.
    """
    from django.db import IntegrityError

    last_error = None
    for attempt in range(1, CART_OPTIMISTIC_MAX_RETRIES + 1):
        try:
            result = _try_add_to_cart_optimistic(user, product_id, quantity)
            if result is not None:
                return result
            # result is None → version conflict → back off and retry
            logger.warning(
                "Cart optimistic retry %s/%s user=%s product=%s",
                attempt, CART_OPTIMISTIC_MAX_RETRIES, user.id, product_id,
            )
            time.sleep(CART_OPTIMISTIC_RETRY_DELAY * attempt)
        except ValueError:
            raise   # not enough stock — no point retrying
        except IntegrityError:
            # Rare race on INSERT (unique_together) — treat as conflict
            logger.warning(
                "Cart IntegrityError (duplicate insert race) attempt=%s "
                "user=%s product=%s — retrying",
                attempt, user.id, product_id,
            )
            time.sleep(CART_OPTIMISTIC_RETRY_DELAY * attempt)
        except Exception as exc:
            last_error = exc
            break

    raise RuntimeError(
        f"Could not update cart for user={user.id} product={product_id} "
        f"after {CART_OPTIMISTIC_MAX_RETRIES} optimistic retries."
        + (f" Last error: {last_error}" if last_error else "")
    )


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

    Why Pessimistic here?
      "Set quantity to N" semantics require the last writer to win with no
      ambiguity. Pessimistic locking serializes access so the final value
      is always deterministic.

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
    item.version += 1
    item.save(update_fields=["quantity", "version", "updated_at"])
    logger.info(
        "Cart quantity set (pessimistic): user=%s product=%s qty=%s",
        user.id, product_id, quantity,
    )
    return item
