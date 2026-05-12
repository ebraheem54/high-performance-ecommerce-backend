"""
═══════════════════════════════════════════════════════════════════════
DEMO ONLY — Unsafe Checkout (NO Pessimistic Locking)
═══════════════════════════════════════════════════════════════════════

WHY THE RACE CONDITION HAPPENS HERE:

  Safe version (services.py):
    SELECT ... FOR UPDATE   ← locks product rows
    check stock             ← under lock, no other transaction can change stock
    UPDATE stock = stock-1  ← safe, serialised

  Unsafe version (this file):
    SELECT (no lock)        ← T1 reads stock=10
                            ← T2 reads stock=10  (simultaneously!)
    sleep(0.05)             ← widens the race window intentionally
    check stock             ← T1: 10>=1 ✓   T2: 10>=1 ✓  (both pass!)
    UPDATE stock=F-1        ← T1: stock→9   T2: stock→8 ... T20: stock→-10

  Result: all 20 users succeed, stock goes NEGATIVE — overselling proven.

Key differences from the safe version:
  ✗  NO select_for_update()         — no row lock acquired
  ✗  F('stock') SQL expression      — real decrement, not snapshot value
  ✗  sleep between check & update   — widens the concurrency window
  ✗  NO @transaction.atomic         — each step in its own auto-commit

Used exclusively by /api/orders/checkout-unsafe/ for Requirement 1 demo.
DO NOT use in production.
═══════════════════════════════════════════════════════════════════════
"""

import time
import logging
from django.db.models import F
from apps.orders.models import Order, OrderItem, Payment

logger = logging.getLogger(__name__)


def create_order_from_cart_unsafe(user) -> Order:
    """
    ⚠ NO LOCKING — intentional race condition for demo.

    Deliberately NOT wrapped in @transaction.atomic so the CHECK
    and the UPDATE run as separate auto-commit statements.
    This maximises the concurrency window for the race condition.

    Step-by-step race:
      T1: SELECT stock=10  (no lock)
      T2: SELECT stock=10  (no lock — T1 hasn't committed anything yet)
      T1: sleep 50ms       ← race window open
      T2: sleep 50ms       ← race window open
      T1: CHECK 10>=1 ✓ → UPDATE stock=F('stock')-1 → stock=9
      T2: CHECK 10>=1 ✓ → UPDATE stock=F('stock')-1 → stock=8
      ...
      T20: CHECK 10>=1 ✓ → UPDATE stock=F('stock')-1 → stock=-10  ← OVERSELL!
    """
    from apps.cart.models import CartItem
    from apps.products.models import Product

    # ── Step 1: Read cart (no transaction, no lock) ───────────────────────────
    cart_items = list(
        CartItem.objects.select_related("product").filter(user=user)
    )
    if not cart_items:
        raise ValueError("Cart is empty.")

    product_ids = [item.product_id for item in cart_items]

    # ⚠ Plain SELECT — no FOR UPDATE, no lock acquired
    products = {
        p.id: p
        for p in Product.objects.filter(id__in=product_ids)
    }

    logger.warning(
        "[UNSAFE] ⚠ NO LOCK — reading stock snapshot for products=%s user=%s",
        product_ids, user.id,
    )

    # ── Step 2: Stock check against snapshot (STALE read) ────────────────────
    # This check passes for ALL concurrent transactions because none of them
    # has committed an UPDATE yet — they all see stock=10.
    for item in cart_items:
        product = products.get(item.product_id)
        if not product:
            raise ValueError(f"Product {item.product_id} not found.")
        if product.stock < item.quantity:
            raise ValueError(
                f"[UNSAFE] Snapshot says insufficient stock for "
                f"'{product.name}': {product.stock} available."
            )

    logger.warning(
        "[UNSAFE] ✓ Stock check PASSED for user=%s (snapshot stock=%s) — "
        "but this snapshot may already be stale!",
        user.id, [products[item.product_id].stock for item in cart_items],
    )

    # ── Step 3: Intentional delay — widens the race window ───────────────────
    # In production, this delay represents realistic work (payment validation,
    # fraud checks, etc.). Here we exaggerate it to make the race visible.
    # While we sleep, other transactions also pass the check above.
    time.sleep(0.05)   # 50ms window — all concurrent users are here simultaneously

    # ── Step 4: Deduct stock using SQL F() expression ─────────────────────────
    # CRITICAL: F('stock') generates SQL  →  UPDATE SET stock = stock - 1
    # NOT Python value →  UPDATE SET stock = 9 (which was the old broken demo)
    #
    # With F():  each transaction subtracts from whatever stock IS at UPDATE time
    # → T1 commits: stock = 10 - 1 = 9
    # → T2 commits: stock = 9  - 1 = 8   (T1 already committed!)
    # → T3 commits: stock = 8  - 1 = 7
    # ...
    # → T20 commits: stock = -10   ← NEGATIVE → OVERSELL PROVEN
    for item in cart_items:
        updated = Product.objects.filter(id=item.product_id).update(
            stock=F("stock") - item.quantity   # ← SQL expression, real decrement
        )
        logger.warning(
            "[UNSAFE] ⚠ UPDATE stock=F-1 on product=%s user=%s "
            "(rows_updated=%s, no lock held!)",
            item.product_id, user.id, updated,
        )

    # ── Step 5: Create order records ──────────────────────────────────────────
    total = sum(
        products[item.product_id].price * item.quantity
        for item in cart_items
    )

    order = Order.objects.create(
        user=user,
        status=Order.Status.CONFIRMED,
        total_price=total,
    )

    OrderItem.objects.bulk_create([
        OrderItem(
            order=order,
            product_id=item.product_id,
            quantity=item.quantity,
            unit_price=products[item.product_id].price,
        )
        for item in cart_items
    ])

    Payment.objects.create(
        order=order,
        amount=total,
        method=Payment.Method.CREDIT_CARD,
    )

    CartItem.objects.filter(user=user).delete()

    # Re-read actual stock from DB to show in log (the real damage)
    actual_stock = Product.objects.filter(id__in=product_ids).values("id", "stock")
    actual_map   = {p["id"]: p["stock"] for p in actual_stock}

    logger.warning(
        "[UNSAFE] ⚠ Order #%s created for user=%s — "
        "ACTUAL stock after deduction: %s (may be negative!)",
        order.id, user.id,
        {pid: actual_map.get(pid, "?") for pid in product_ids},
    )

    return order
