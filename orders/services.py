"""
Business logic for orders.
Handles checkout as a single ACID transaction:
  payment + stock deduction + order creation all succeed or all fail.
"""

from django.db import transaction
from django.utils import timezone

from orders.models import Order, OrderItem, Payment


@transaction.atomic
def create_order_from_cart(user) -> Order:
    """
    Checkout: convert the user's cart into a confirmed order.

    ACID guarantee (Synchronization point):
      All of the following happen in one atomic block:
        1. Read cart items
        2. Deduct stock for each product (with Optimistic Locking)
        3. Create Order + OrderItems
        4. Create Payment record
        5. Clear the cart
      If any step fails → entire transaction rolls back.
    """
    from cart.models import CartItem
    from products.services import deduct_stock_optimistic

    cart_items = CartItem.objects.select_related("product").filter(user=user)
    if not cart_items.exists():
        raise ValueError("Cart is empty.")

    # Step 1: Deduct stock for each item
    for item in cart_items:
        success = deduct_stock_optimistic(item.product_id, item.quantity)
        if not success:
            raise Exception(
                f"Stock conflict for '{item.product.name}'. Please retry."
            )

    # Step 2: Create order
    order = Order.objects.create(user=user, status=Order.Status.PENDING)

    # Step 3: Create order items and calculate total
    total = 0
    for item in cart_items:
        unit_price = item.product.price
        OrderItem.objects.create(
            order=order,
            product=item.product,
            quantity=item.quantity,
            unit_price=unit_price,
        )
        total += unit_price * item.quantity

    order.total_price = total
    order.status = Order.Status.CONFIRMED
    order.save(update_fields=["total_price", "status", "updated_at"])

    # Step 4: Create pending payment record
    Payment.objects.create(
        order=order,
        amount=total,
        status=Payment.Status.PENDING,
        method=Payment.Method.CREDIT_CARD,
    )

    # Step 5: Clear the cart
    cart_items.delete()

    return order


@transaction.atomic
def process_payment(order_id: int, method: str, transaction_id: str = "") -> Payment:
    """
    Mark the payment as completed.
    Updates order status to PROCESSING after successful payment.
    """
    payment = Payment.objects.select_for_update().get(order_id=order_id)

    if payment.status == Payment.Status.COMPLETED:
        raise ValueError("Payment already completed.")

    payment.status = Payment.Status.COMPLETED
    payment.method = method
    payment.transaction_id = transaction_id
    payment.save(update_fields=["status", "method", "transaction_id", "updated_at"])

    payment.order.status = Order.Status.PROCESSING
    payment.order.save(update_fields=["status", "updated_at"])

    return payment


def get_user_orders(user):
    """Return all orders for a user, newest first."""
    return Order.objects.filter(user=user).prefetch_related("items__product").order_by("-created_at")


def cancel_order(order_id: int, user=None) -> Order:
    """
    Cancel an order if it is still in PENDING or CONFIRMED state.
    user=None means admin is cancelling (no ownership check).
    """
    with transaction.atomic():
        filters = {"id": order_id}
        if user is not None:
            filters["user"] = user
        order = Order.objects.select_for_update().get(**filters)
        if order.status not in [Order.Status.PENDING, Order.Status.CONFIRMED]:
            raise ValueError(f"Cannot cancel order in status: {order.status}")
        order.status = Order.Status.CANCELLED
        order.save(update_fields=["status", "updated_at"])
    return order


@transaction.atomic
def update_order_status(order_id: int, new_status: str) -> Order:
    """Admin: update order status to any valid value."""
    order = Order.objects.select_for_update().get(id=order_id)
    order.status = new_status
    order.save(update_fields=["status", "updated_at"])
    return order
