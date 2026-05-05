"""
Business logic for orders.
Handles checkout as a single ACID transaction:
  payment + stock deduction + order creation all succeed or all fail.
"""
import logging
from django.db import transaction
from django.utils import timezone
from apps.orders.models import Order, OrderItem, Payment

logger = logging.getLogger(__name__)



@transaction.atomic
def create_order_from_cart(user) -> Order:
    """
    ═══ PESSIMISTIC LOCKING — Synchronization Points ═══════════════════════════
    Point 1 — Product rows locked FIRST:
      Products.objects.select_for_update(nowait=False).filter(id__in=[...])
      → PostgreSQL acquires row-level FOR UPDATE locks on every product
        in the cart at once, in a consistent order (ordered by id to
        avoid deadlocks).
      → Any other transaction trying to checkout with the same products
        will BLOCK until this transaction commits or rolls back.
    Point 2 — CartItems read inside the same transaction:
      CartItem.objects.select_related("product").filter(user=user)
      → Guarantees we see a consistent snapshot of the cart.
    ACID properties:
      Atomicity  — all DB writes wrapped in transaction.atomic().
      Consistency — stock cannot go below 0 (ValueError guard).
      Isolation  — FOR UPDATE prevents phantom reads on product rows.
      Durability — PostgreSQL WAL guarantees committed data survives crash.
    Why NOT Optimistic here?
      Optimistic locking requires retry on conflict. In a checkout flow:
        - The user is waiting actively (response time matters)
        - Payment may already be initiated
        - Retrying a partial order is complex and risky
      Pessimistic locking blocks briefly but guarantees one clean pass.
    """
    from apps.cart.models import CartItem
    from apps.products.models import Product
    from apps.products.services import deduct_stock_pessimistic

    # Read cart items (evaluate to a list so we can iterate multiple times)
    cart_items = list(
        CartItem.objects
        .select_related("product")
        .filter(user=user)
    )
    if not cart_items:
        raise ValueError("Cart is empty.")

    # Collect product IDs — sort them to acquire locks in a consistent order.
    # IMPORTANT: Always lock rows in the SAME ORDER across all transactions
    # to prevent deadlocks (classic database concurrency best practice).
    product_ids = sorted(item.product_id for item in cart_items)

    # ── Synchronization Point 1: Pessimistic Lock on all product rows ─────────
    # select_for_update() issues: SELECT ... FOR UPDATE
    # This blocks any other transaction that tries to modify these rows.
    locked_products = {
        p.id: p
        for p in Product.objects.select_for_update().filter(id__in=product_ids).order_by("id")
    }
    logger.info(
        "Pessimistic locks acquired on products %s for user=%s",
        product_ids, user.id,
    )

    # ── Synchronization Point 2: Validate stock under lock ────────────────────
    # Since we hold the locks, no other transaction can change stock values.
    # This validation is now race-condition-free.
    for item in cart_items:
        locked_product = locked_products.get(item.product_id)
        if not locked_product:
            raise ValueError(f"Product '{item.product.name}' is no longer available.")
        if locked_product.stock < item.quantity:
            raise ValueError(
                f"Insufficient stock for '{locked_product.name}': "
                f"{locked_product.stock} available, {item.quantity} requested."
            )

    # ── Deduct stock (pessimistic path — product rows already locked) ─────────
    for item in cart_items:
        locked_product = locked_products[item.product_id]
        deduct_stock_pessimistic(locked_product, item.quantity)

    # ── Compute total BEFORE creating the order ────────────────────────────────
    # Use the locked product price as the authoritative price at purchase time.
    total = sum(
        locked_products[item.product_id].price * item.quantity
        for item in cart_items
    )

    # ── Create order ──────────────────────────────────────────────────────────
    order = Order.objects.create(
        user=user,
        status=Order.Status.PENDING,
        total_price=total,
    )

    # ── Create OrderItem records for every cart item ───────────────────────────
    # This is critical: without OrderItems the order is empty.
    OrderItem.objects.bulk_create([
        OrderItem(
            order=order,
            product_id=item.product_id,
            quantity=item.quantity,
            unit_price=locked_products[item.product_id].price,
        )
        for item in cart_items
    ])

    # Mark order confirmed after all items and stock changes are persisted
    order.status = Order.Status.CONFIRMED
    order.save(update_fields=["status", "updated_at"])

    # ── Synchronization Point 3: Create Payment record (pessimistic) ──────────
    # Payment is created inside the same atomic block.
    # No other transaction can see this order until we commit.
    Payment.objects.create(
        order=order,
        amount=total,
        method=Payment.Method.CREDIT_CARD,
    )

    # ── Clear cart ────────────────────────────────────────────────────────────
    CartItem.objects.filter(user=user).delete()

    logger.info(
        "Order #%s created for user=%s total=%s (pessimistic checkout complete)",
        order.id, user.id, total,
    )
    return order



@transaction.atomic
def process_payment(order_id: int, method: str, transaction_id: str = "") -> Payment:
    """
    Mark the payment as completed.
    PESSIMISTIC LOCKING
    Synchronization point: select_for_update() on both Order and Payment.
      → Locks both rows exclusively to prevent double-payment and
        inconsistent order/payment state.
    Why absolutely Pessimistic here?
      A payment being processed twice is a financial error that cannot be
      undone. We MUST serialize access. Optimistic locking with retry would
      be dangerous — a failed retry might re-attempt charging the user.
    """
    # ── Lock order row FIRST, then payment — consistent lock ordering ─────────
    order = Order.objects.select_for_update().get(id=order_id)
    payment = Payment.objects.select_for_update().get(order_id=order_id)

    logger.info(
        "Pessimistic locks acquired on Order=%s and Payment for order=%s",
        order_id, order_id,
    )

    if payment.status == Payment.Status.COMPLETED:
        raise ValueError("Payment already completed.")

    payment.status = Payment.Status.COMPLETED
    payment.method = method
    payment.transaction_id = transaction_id
    payment.save(update_fields=["status", "method", "transaction_id", "updated_at"])

    order.status = Order.Status.PROCESSING
    order.save(update_fields=["status", "updated_at"])

    logger.info("Payment completed for order=%s method=%s", order_id, method)
    return payment



def get_user_orders(user):
    """Return all orders for a user, newest first."""
    return (
        Order.objects
        .filter(user=user)
        .prefetch_related("items__product")
        .order_by("-created_at")
    )

@transaction.atomic
def cancel_order(order_id: int, user=None) -> Order:
    """
    Cancel an order if it is still in PENDING or CONFIRMED state.
    PESSIMISTIC LOCKING: Locks the order row to prevent a race where
    two requests try to cancel/process the same order simultaneously.
    user=None means admin is cancelling (no ownership check).
    @transaction.atomic ensures the lock (select_for_update) is valid —
    select_for_update MUST be inside an active transaction to hold the lock.
    """
    filters = {"id": order_id}
    if user is not None:
        filters["user"] = user
    # Synchronization point: lock order row
    order = Order.objects.select_for_update().get(**filters)
    if order.status not in [Order.Status.PENDING, Order.Status.CONFIRMED]:
        raise ValueError(f"Cannot cancel order in status: {order.status}")
    order.status = Order.Status.CANCELLED
    order.save(update_fields=["status", "updated_at"])
    logger.info("Order #%s cancelled by user=%s", order_id, user)
    return order


@transaction.atomic
def update_order_status(order_id: int, new_status: str) -> Order:
    """
    Admin: update order status to any valid value.
    PESSIMISTIC LOCKING to prevent concurrent status updates.
    """
    # Synchronization point: lock order row
    order = Order.objects.select_for_update().get(id=order_id)
    order.status = new_status
    order.save(update_fields=["status", "updated_at"])
    logger.info("Order #%s status updated to %s", order_id, new_status)
    return order
