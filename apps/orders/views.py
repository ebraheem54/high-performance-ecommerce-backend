"""
API endpoints for orders.

Permissions:
  - Customer: checkout, view/cancel OWN orders only
  - Admin   : view ALL orders, update status, cancel any order
"""

import logging

from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from apps.orders import services
from apps.orders.models import Order, Payment
from apps.orders.serializers import OrderSerializer, OrderListSerializer
from apps.core.logging_utils import log_user_error, log_user_event, log_user_warning

logger = logging.getLogger(__name__)


def _log_order_issue(
    request,
    event: str,
    response_status: int,
    reason: str,
    level: str = "warning",
    **fields,
) -> None:
    user_id = getattr(request.user, "id", None)
    log_message = (
        "order_issue level=%s event=%s user=%s status=%s reason=%s fields=%s"
    )
    if level == "error":
        logger.error(log_message, level, event, user_id, response_status, reason, fields)
        log_user_error(user_id, event, status=response_status, reason=reason, **fields)
    elif level == "info":
        logger.info(log_message, level, event, user_id, response_status, reason, fields)
        log_user_event(user_id, event, status=response_status, reason=reason, **fields)
    else:
        logger.warning(log_message, level, event, user_id, response_status, reason, fields)
        log_user_warning(user_id, event, status=response_status, reason=reason, **fields)


# Customer endpoints
class MyOrderListView(generics.ListAPIView):
    """
    GET /api/orders/
    Customer: see only their own orders.
    Admin:    sees ALL orders.
    """
    serializer_class   = OrderListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_staff:
            return Order.objects.prefetch_related("items__product").order_by("-created_at")
        return services.get_user_orders(self.request.user)


class MyOrderDetailView(generics.RetrieveAPIView):
    """
    GET /api/orders/<id>/
    Customer: own orders only. Admin: any order.
    """
    serializer_class   = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_staff:
            return Order.objects.prefetch_related("items__product")
        return Order.objects.filter(user=self.request.user).prefetch_related("items__product")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def checkout_view(request):
    """
    POST /api/orders/checkout/
    Customer only — converts cart into a confirmed order (ACID transaction).
    """
    if request.user.is_staff:
        _log_order_issue(
            request,
            "order.checkout",
            status.HTTP_403_FORBIDDEN,
            "Admins cannot place orders.",
            level="info",
        )
        return Response(
            {"error": "Admins cannot place orders."},
            status=status.HTTP_403_FORBIDDEN,
        )
    try:
        order = services.create_order_from_cart(request.user)
    except ValueError as e:
        _log_order_issue(
            request,
            "order.checkout",
            status.HTTP_400_BAD_REQUEST,
            str(e),
            level="warning",
        )
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        _log_order_issue(
            request,
            "order.checkout",
            status.HTTP_409_CONFLICT,
            str(e),
            level="warning",
        )
        return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)

    try:
        from apps.orders.tasks import send_order_confirmation_email
        send_order_confirmation_email.delay(order.id)
    except Exception:
        pass

    return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wallet_balance_view(request):
    """
    GET /api/orders/wallet/balance/
    Return the user's current wallet balance.
    """
    return Response({
        "wallet_balance": float(request.user.wallet_balance),
        "currency": "USD",
        "user": request.user.email,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def checkout_wallet_sync_view(request):
    """
    POST /api/orders/checkout-wallet-sync/

    Demo endpoint: wallet payment runs synchronously inside the request.
    Compare with checkout-wallet-async, which returns before the worker runs.
    """
    import time as _time

    if request.user.is_staff:
        return Response({"error": "Admins cannot place orders."}, status=status.HTTP_403_FORBIDDEN)

    started = _time.time()
    try:
        order = services.checkout_with_wallet(request.user)
    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)

    total_elapsed = round(_time.time() - started, 3)

    data = OrderSerializer(order).data
    data["_demo"] = {
        "mode"           : "BEFORE — synchronous payment",
        "warning"        : "⚠ HTTP response blocked for ~3s (payment gateway simulation)",
        "total_elapsed_s": total_elapsed,
        "compare_with"   : "POST /api/orders/checkout-wallet-async/ (async — returns in <300ms)",
        "wallet_balance" : float(request.user.wallet_balance),
    }
    return Response(data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def checkout_wallet_async_view(request):
    """
    POST /api/orders/checkout-wallet-async/

    Demo endpoint: wallet payment runs in a Celery task and returns 202.
    """
    if request.user.is_staff:
        return Response({"error": "Admins cannot place orders."}, status=status.HTTP_403_FORBIDDEN)

    try:
        from apps.orders.tasks import process_wallet_payment_async
        task = process_wallet_payment_async.delay(request.user.id)
    except Exception as e:
        return Response({"error": f"Queue unavailable: {e}"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response({
        "status"     : "queued",
        "task_id"    : task.id,
        "message"    : "Your payment was queued and will be processed in the background.",
        "wallet_balance": float(request.user.wallet_balance),
        "_demo"      : {
            "mode"      : "AFTER — async payment via Celery",
            "note"      : "Payment gateway sleep(3s) runs in background — HTTP returned immediately",
            "check_task": f"GET /api/orders/ (after a few seconds to see completed order)",
        },
    }, status=status.HTTP_202_ACCEPTED)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def cancel_order_view(request, order_id):
    """
    POST /api/orders/<id>/cancel/
    Customer: can cancel only their own orders.
    Admin: can cancel any order.
    """
    try:
        user  = None if request.user.is_staff else request.user
        order = services.cancel_order(order_id, user)
    except Order.DoesNotExist:
        _log_order_issue(
            request,
            "order.cancel",
            status.HTTP_404_NOT_FOUND,
            "Order not found.",
            level="info",
            order_id=order_id,
        )
        return Response({"error": "Order not found."}, status=status.HTTP_404_NOT_FOUND)
    except ValueError as e:
        _log_order_issue(
            request,
            "order.cancel",
            status.HTTP_400_BAD_REQUEST,
            str(e),
            level="warning",
            order_id=order_id,
        )
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    try:
        from apps.orders.tasks import send_order_cancelled_email
        send_order_cancelled_email.delay(order.id)
    except Exception:
        pass

    return Response(OrderSerializer(order).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def cancel_order_unsafe_view(request, order_id):
    """
    POST /api/orders/<id>/cancel-unsafe/

    DEMO ONLY — Requirement 1 BEFORE state.
    Intentionally does not lock the order row and does not enforce the order
    state machine. This can create an invalid paid-and-cancelled state.
    """
    import time as _time

    try:
        order = Order.objects.get(id=order_id)
        if not request.user.is_staff and order.user_id != request.user.id:
            raise Order.DoesNotExist
    except Order.DoesNotExist:
        return Response({"error": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

    previous_status = order.status
    payment_status = getattr(getattr(order, "payment", None), "status", None)

    _time.sleep(0.10)
    order.status = Order.Status.CANCELLED
    order.save(update_fields=["status", "updated_at"])

    return Response({
        "order_id": order.id,
        "previous_status": previous_status,
        "order_status": order.status,
        "payment_status": payment_status,
        "paid_and_cancelled": payment_status == Payment.Status.COMPLETED,
        "warning": "NO LOCKING — unsafe cancel demo endpoint",
    }, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def process_payment_view(request, order_id):
    """
    POST /api/orders/<order_id>/process-payment/
    Customer: process payment for their own order only.
    Admin: process payment for any order.
    Body: { "method": "credit_card", "transaction_id": "demo-123" }

    Requirement 1 demo endpoint:
    exposes process_payment(), which locks both Order and Payment rows with
    select_for_update() to prevent double payment processing.
    """
    method = request.data.get("method", "credit_card")
    transaction_id = request.data.get("transaction_id", "")

    try:
        payment = services.process_payment(order_id, method, transaction_id, request.user)
    except Order.DoesNotExist:
        _log_order_issue(
            request,
            "payment.process",
            status.HTTP_404_NOT_FOUND,
            "Order not found.",
            level="info",
            order_id=order_id,
        )
        return Response({"error": "Order not found."}, status=status.HTTP_404_NOT_FOUND)
    except Exception as exc:
        _log_order_issue(
            request,
            "payment.process",
            status.HTTP_400_BAD_REQUEST,
            str(exc),
            level="warning",
            order_id=order_id,
            method=method,
            transaction_id=transaction_id,
        )
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        "order_id": order_id,
        "payment_id": payment.id,
        "payment_status": payment.status,
        "method": payment.method,
        "transaction_id": payment.transaction_id,
    }, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def process_payment_unsafe_view(request, order_id):
    """
    POST /api/orders/<order_id>/process-payment-unsafe/

    DEMO ONLY — Requirement 1 BEFORE state.
    Intentionally does not lock Order/Payment rows and does not block an
    already-completed payment. This simulates a duplicate charge bug.
    """
    import time as _time

    method = request.data.get("method", "credit_card")
    transaction_id = request.data.get("transaction_id", "")

    try:
        order = Order.objects.get(id=order_id)
        if not request.user.is_staff and order.user_id != request.user.id:
            raise Order.DoesNotExist
        payment = Payment.objects.get(order_id=order_id)
    except Order.DoesNotExist:
        return Response({"error": "Order not found."}, status=status.HTTP_404_NOT_FOUND)
    except Payment.DoesNotExist:
        return Response({"error": "Payment not found."}, status=status.HTTP_404_NOT_FOUND)

    was_completed = payment.status == Payment.Status.COMPLETED
    _time.sleep(0.10)

    payment.status = Payment.Status.COMPLETED
    payment.method = method
    payment.transaction_id = transaction_id
    payment.save(update_fields=["status", "method", "transaction_id", "updated_at"])

    order.status = Order.Status.PROCESSING
    order.save(update_fields=["status", "updated_at"])

    return Response({
        "order_id": order_id,
        "payment_id": payment.id,
        "payment_status": payment.status,
        "method": payment.method,
        "transaction_id": payment.transaction_id,
        "duplicate_processed": was_completed,
        "warning": "NO LOCKING — unsafe duplicate payment demo endpoint",
    }, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def race_demo_view(request):
    """
    POST /api/orders/race-demo/
    Body: { "product_id": <int> }

    Demo endpoint: direct race condition without cart, locks, or stock floor.
    This intentionally demonstrates overselling behavior.
    """
    import time
    from django.db.models import F
    from apps.products.models import Product

    if request.user.is_staff:
        return Response({"error": "Admins cannot place orders."}, status=status.HTTP_403_FORBIDDEN)

    product_id = request.data.get("product_id")
    if not product_id:
        return Response({"error": "product_id required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        product = Product.objects.get(id=product_id, is_active=True)
    except Product.DoesNotExist:
        return Response({"error": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

    stock_snapshot = product.stock
    time.sleep(0.10)
    Product.objects.filter(id=product_id).update(stock=F("stock") - 1)
    actual_stock = Product.objects.get(id=product_id).stock

    from apps.orders.models import Order, OrderItem, Payment
    order = Order.objects.create(
        user=request.user,
        status=Order.Status.CONFIRMED,
        total_price=product.price,
    )
    OrderItem.objects.create(order=order, product_id=product_id, quantity=1, unit_price=product.price)
    Payment.objects.create(order=order, amount=product.price, method=Payment.Method.CREDIT_CARD)

    import logging
    logging.getLogger(__name__).warning(
        "[RACE-DEMO] ⚠ Order #%s created for user=%s | "
        "snapshot_stock=%s | actual_stock_now=%s %s",
        order.id, request.user.id, stock_snapshot, actual_stock,
        "← OVERSELL!" if actual_stock < 0 else "",
    )

    return Response({
        "order_id"      : order.id,
        "product_id"    : product_id,
        "snapshot_stock": stock_snapshot,
        "actual_stock"  : actual_stock,
        "oversell"      : actual_stock < 0,
        "warning"       : "⚠ NO LOCKING — race condition demo endpoint",
    }, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def checkout_sync_demo_view(request):
    """
    POST /api/orders/checkout-sync/

    Demo endpoint: simulates synchronous email in the checkout path.
    The user waits for the full email delay before getting a response.
    """
    import time as _time

    if request.user.is_staff:
        return Response(
            {"error": "Admins cannot place orders."},
            status=status.HTTP_403_FORBIDDEN,
        )

    started = _time.time()

    try:
        order = services.create_order_from_cart(request.user)
    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)

    order_elapsed = round(_time.time() - started, 3)

    import logging as _logging
    _log = _logging.getLogger(__name__)
    _log.warning(
        "[SYNC-EMAIL] ⚠ DEMO — Sending confirmation email SYNCHRONOUSLY "
        "for Order #%s — blocking HTTP response for 2s ...",
        order.id,
    )

    _time.sleep(2)

    _log.warning(
        "[SYNC-EMAIL] ⚠ DEMO — Email 'sent' synchronously for Order #%s "
        "(user waited the full delay before receiving HTTP 201)",
        order.id,
    )

    total_elapsed = round(_time.time() - started, 3)

    data = OrderSerializer(order).data
    data["_demo"] = {
        "warning"        : "⚠ DEMO ONLY — synchronous email simulation",
        "order_time_s"   : order_elapsed,
        "email_delay_s"  : 2.0,
        "total_elapsed_s": total_elapsed,
        "compare_with"   : "POST /api/orders/checkout/ (async Celery — returns in <300ms)",
    }
    return Response(data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([IsAdminUser])
def update_order_status_view(request, order_id):
    """
    PATCH /api/orders/<id>/status/    — admin only
    Body: { "status": "shipped" }
    Valid values: pending, confirmed, processing, shipped, delivered, cancelled
    """
    new_status = request.data.get("status")
    valid = [s.value for s in Order.Status]
    if new_status not in valid:
        return Response(
            {"error": f"Invalid status. Choose from: {valid}"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        order = services.update_order_status(order_id, new_status)
    except Order.DoesNotExist:
        return Response({"error": "Order not found."}, status=status.HTTP_404_NOT_FOUND)
    return Response(OrderSerializer(order).data)
