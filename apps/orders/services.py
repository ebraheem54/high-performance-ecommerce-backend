"""
Business logic for orders.
Handles checkout as a single ACID transaction:
  payment + stock deduction + order creation all succeed or all fail.
"""
import logging
import time
from django.db import transaction
from django.utils import timezone
from apps.orders.models import Order, OrderItem, Payment
from apps.core.logging_utils import log_service_call, log_user_event

payment_logger = logging.getLogger("payments")



@transaction.atomic
@log_service_call(
    "order.checkout",
    context_builder=lambda args: {"user_id": args["user"].id},
    result_builder=lambda result, args: {"order_id": result.id},
)
def create_order_from_cart(user) -> Order:
    """
    Create an order from the user's cart in a single ACID transaction.

    Product rows are locked in a deterministic order to prevent overselling
    and avoid deadlocks when multiple users buy the same products.
    """
    from apps.cart.models import CartItem
    from apps.products.models import Product
    from apps.products.services import deduct_stock_pessimistic

    cart_items = list(
        CartItem.objects
        .select_related("product")
        .filter(user=user)
    )
    if not cart_items:
        raise ValueError("Cart is empty.")

    # Lock products in a stable order to avoid deadlocks.
    product_ids = sorted(item.product_id for item in cart_items)

    locked_products = {
        p.id: p
        for p in Product.objects.select_for_update().filter(id__in=product_ids).order_by("id")
    }
    for item in cart_items:
        locked_product = locked_products.get(item.product_id)
        if not locked_product:
            raise ValueError(f"Product '{item.product.name}' is no longer available.")
        if locked_product.stock < item.quantity:
            raise ValueError(
                f"Insufficient stock for '{locked_product.name}': "
                f"{locked_product.stock} available, {item.quantity} requested."
            )

    for item in cart_items:
        locked_product = locked_products[item.product_id]
        deduct_stock_pessimistic(locked_product, item.quantity)

    # Use the locked product price as the authoritative price at purchase time.
    total = sum(
        locked_products[item.product_id].price * item.quantity
        for item in cart_items
    )

    order = Order.objects.create(
        user=user,
        status=Order.Status.PENDING,
        total_price=total,
    )

    OrderItem.objects.bulk_create([
        OrderItem(
            order=order,
            product_id=item.product_id,
            quantity=item.quantity,
            unit_price=locked_products[item.product_id].price,
        )
        for item in cart_items
    ])

    order.status = Order.Status.CONFIRMED
    order.save(update_fields=["status", "updated_at"])

    Payment.objects.create(
        order=order,
        amount=total,
        method=Payment.Method.CREDIT_CARD,
    )

    CartItem.objects.filter(user=user).delete()

    log_user_event(
        user.id,
        "order.checkout",
        order_id=order.id,
        total=total,
        items=len(cart_items),
        lock="pessimistic",
        result="created",
    )

    # Stock changed, so the public product list cache is no longer current.
    from django.core.cache import cache
    cache.delete("product_list")

    return order



@transaction.atomic
@log_service_call(
    "payment.process",
    context_builder=lambda args: {"order_id": args["order_id"], "method": args["method"]},
    result_builder=lambda result, args: {"payment_id": result.id, "status": result.status},
)
def process_payment(order_id: int, method: str, transaction_id: str = "", user=None) -> Payment:
    # Lock order first, then payment, matching the project's lock ordering.
    order = Order.objects.select_for_update().get(id=order_id)
    if user is not None and not user.is_staff and order.user_id != user.id:
        raise Order.DoesNotExist
    payment = Payment.objects.select_for_update().get(order_id=order_id)

    if order.status == Order.Status.CANCELLED:
        raise ValueError("Cannot process payment for a cancelled order.")

    if payment.status == Payment.Status.COMPLETED:
        raise ValueError("Payment already completed.")

    payment.status = Payment.Status.COMPLETED
    payment.method = method
    payment.transaction_id = transaction_id
    payment.save(update_fields=["status", "method", "transaction_id", "updated_at"])

    order.status = Order.Status.PROCESSING
    order.save(update_fields=["status", "updated_at"])

    payment_logger.info(
        "payment.completed user=%s order_id=%s payment_id=%s method=%s transaction_id=%s",
        order.user_id,
        order_id,
        payment.id,
        method,
        transaction_id,
    )
    log_user_event(
        order.user_id,
        "payment.process",
        order_id=order_id,
        payment_id=payment.id,
        method=method,
        status=payment.status,
        lock="pessimistic",
        result="completed",
    )
    return payment



@transaction.atomic
@log_service_call(
    "order.checkout_wallet",
    context_builder=lambda args: {"user_id": args["user"].id},
    result_builder=lambda result, args: {"order_id": result.id},
)
def checkout_with_wallet(user) -> Order:
    """
    Complete a wallet checkout in one transaction.

    The user row protects wallet balance, product rows protect stock, and the
    simulated payment delay represents an external payment gateway call.
    """
    from apps.cart.models import CartItem
    from apps.products.models import Product
    from apps.products.services import deduct_stock_pessimistic

    from apps.users.models import User as UserModel
    locked_user = UserModel.objects.select_for_update().get(pk=user.pk)

    cart_items = list(
        CartItem.objects
        .select_related("product")
        .filter(user=locked_user)
    )
    if not cart_items:
        raise ValueError("Cart is empty.")

    product_ids = sorted(item.product_id for item in cart_items)

    locked_products = {
        p.id: p
        for p in Product.objects.select_for_update().filter(id__in=product_ids).order_by("id")
    }

    for item in cart_items:
        locked_product = locked_products.get(item.product_id)
        if not locked_product:
            raise ValueError(f"Product '{item.product.name}' is no longer available.")
        if locked_product.stock < item.quantity:
            raise ValueError(
                f"Insufficient stock for '{locked_product.name}': "
                f"{locked_product.stock} available, {item.quantity} requested."
            )

    total = sum(
        locked_products[item.product_id].price * item.quantity
        for item in cart_items
    )

    if locked_user.wallet_balance < total:
        raise ValueError(
            f"Insufficient wallet balance: "
            f"current balance {locked_user.wallet_balance:.2f}, "
            f"order total {total:.2f}."
        )

    time.sleep(3)

    locked_user.wallet_balance -= total
    locked_user.save(update_fields=["wallet_balance", "updated_at"])

    for item in cart_items:
        deduct_stock_pessimistic(locked_products[item.product_id], item.quantity)

    order = Order.objects.create(
        user=locked_user,
        status=Order.Status.PENDING,
        total_price=total,
    )
    OrderItem.objects.bulk_create([
        OrderItem(
            order=order,
            product_id=item.product_id,
            quantity=item.quantity,
            unit_price=locked_products[item.product_id].price,
        )
        for item in cart_items
    ])
    order.status = Order.Status.CONFIRMED
    order.save(update_fields=["status", "updated_at"])

    Payment.objects.create(
        order=order,
        amount=total,
        method=Payment.Method.WALLET,
        status=Payment.Status.COMPLETED,
    )

    CartItem.objects.filter(user=locked_user).delete()

    from django.core.cache import cache
    cache.delete("product_list")

    log_user_event(
        locked_user.id,
        "order.checkout_wallet",
        order_id=order.id,
        total=total,
        wallet_remaining=locked_user.wallet_balance,
        lock="pessimistic",
        result="created",
    )
    return order


def get_user_orders(user):
    """Return all orders for a user, newest first."""
    return (
        Order.objects
        .filter(user=user)
        .prefetch_related("items__product")
        .order_by("-created_at")
    )

@transaction.atomic
@log_service_call(
    "order.cancel",
    context_builder=lambda args: {"order_id": args["order_id"]},
    result_builder=lambda result, args: {"status": result.status},
)
def cancel_order(order_id: int, user=None) -> Order:
    
    filters = {"id": order_id}
    if user is not None:
        filters["user"] = user
    # Synchronization point: lock order row
    order = Order.objects.select_for_update().get(**filters)
    if order.status not in [Order.Status.PENDING, Order.Status.CONFIRMED]:
        raise ValueError(f"Cannot cancel order in status: {order.status}")
    order.status = Order.Status.CANCELLED
    order.save(update_fields=["status", "updated_at"])
    log_user_event(
        order.user_id,
        "order.cancel",
        order_id=order_id,
        status=order.status,
        lock="pessimistic",
        result="cancelled",
    )
    return order


@transaction.atomic
@log_service_call(
    "order.status_update",
    context_builder=lambda args: {"order_id": args["order_id"], "new_status": args["new_status"]},
    result_builder=lambda result, args: {"status": result.status},
)
def update_order_status(order_id: int, new_status: str) -> Order:

    # Synchronization point: lock order row
    order = Order.objects.select_for_update().get(id=order_id)
    order.status = new_status
    order.save(update_fields=["status", "updated_at"])
    return order
