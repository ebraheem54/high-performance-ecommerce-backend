"""Unsafe checkout demo used only to show the pre-locking race condition."""

import time
import logging
from django.db.models import F
from apps.orders.models import Order, OrderItem, Payment

logger = logging.getLogger(__name__)


def create_order_from_cart_unsafe(user) -> Order:
    """
    Intentionally skips transaction wrapping and row locks.
    """
    from apps.cart.models import CartItem
    from apps.products.models import Product

    cart_items = list(
        CartItem.objects.select_related("product").filter(user=user)
    )
    if not cart_items:
        raise ValueError("Cart is empty.")

    product_ids = [item.product_id for item in cart_items]

    products = {
        p.id: p
        for p in Product.objects.filter(id__in=product_ids)
    }

    logger.warning(
        "[UNSAFE] ⚠ NO LOCK — reading stock snapshot for products=%s user=%s",
        product_ids, user.id,
    )

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

    time.sleep(0.05)

    for item in cart_items:
        updated = Product.objects.filter(id=item.product_id).update(
            stock=F("stock") - item.quantity
        )
        logger.warning(
            "[UNSAFE] ⚠ UPDATE stock=F-1 on product=%s user=%s "
            "(rows_updated=%s, no lock held!)",
            item.product_id, user.id, updated,
        )

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

    actual_stock = Product.objects.filter(id__in=product_ids).values("id", "stock")
    actual_map   = {p["id"]: p["stock"] for p in actual_stock}

    logger.warning(
        "[UNSAFE] ⚠ Order #%s created for user=%s — "
        "ACTUAL stock after deduction: %s (may be negative!)",
        order.id, user.id,
        {pid: actual_map.get(pid, "?") for pid in product_ids},
    )

    return order
