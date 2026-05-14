"""
API endpoints for orders.

Permissions:
  - Customer: checkout, view/cancel OWN orders only
  - Admin   : view ALL orders, update status, cancel any order
"""

from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from apps.orders import services
from apps.orders.models import Order
from apps.orders.serializers import OrderSerializer, OrderListSerializer


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
        return Response(
            {"error": "Admins cannot place orders."},
            status=status.HTTP_403_FORBIDDEN,
        )
    try:
        order = services.create_order_from_cart(request.user)
    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)

    # ── Async Processing (Requirement 3) ─────────────────────────────────────
    # Fire-and-forget: confirmation email is sent by Celery AFTER this response.
    # The HTTP 201 is returned immediately — the user does NOT wait for the email.
    try:
        from apps.orders.tasks import send_order_confirmation_email
        send_order_confirmation_email.delay(order.id)
    except Exception:
        pass   # never block the response if the queue is temporarily unavailable

    return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


# ══════════════════════════════════════════════════════════════════════════════
# Wallet Payment Endpoints — Requirement 3 (Payment Simulation)
# ══════════════════════════════════════════════════════════════════════════════

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wallet_balance_view(request):
    """
    GET /api/orders/wallet/balance/
    إرجاع رصيد المحفظة الحالي للمستخدم.
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

    ⚠ BEFORE SOLUTION — Req 3 Payment Demo:
    الدفع من المحفظة يحدث SYNCHRONOUSLY داخل HTTP request.
    المستخدم ينتظر 3 ثواني (محاكاة بوابة الدفع) قبل أن يحصل على الـ response.

    تدفق كامل: فحص الرصيد → payment gateway sleep(3s) → خصم → إنشاء الطلب
    Compare with: POST /api/orders/checkout-wallet-async/ ← returns in <300ms
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

    ✅ AFTER SOLUTION — Req 3 Payment Demo:
    الدفع من المحفظة يحدث عبر Celery task في الخلفية.
    المستخدم يحصل على HTTP 202 فوراً — Celery يكمل فحص الرصيد والخصم لاحقاً.

    الفرق الجوهري:
      BEFORE: checkout_wallet_sync → HTTP يحجب 3s
      AFTER : checkout_wallet_async → HTTP يرجع <300ms، Celery يعمل 3s في الخلفية
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
        "message"    : "✅ طلبك في قائمة الانتظار — سيتم معالجة الدفع في الخلفية",
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
        return Response({"error": "Order not found."}, status=status.HTTP_404_NOT_FOUND)
    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # Async cancellation email (Requirement 3) — fire-and-forget
    try:
        from apps.orders.tasks import send_order_cancelled_email
        send_order_cancelled_email.delay(order.id)
    except Exception:
        pass

    return Response(OrderSerializer(order).data)


# ─────────────────────────────────────────────
# Admin-only endpoints
# ─────────────────────────────────────────────
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def race_demo_view(request):
    """
    POST /api/orders/race-demo/
    Body: { "product_id": <int> }

    ⚠ DEMO ONLY — Direct Race Condition (NO cart, NO lock, NO stock floor)
    ════════════════════════════════════════════════════════════════════════
    Bypasses cart entirely. All users target the same product simultaneously.

    Race window (intentional):
      1. Read stock from DB  (no SELECT FOR UPDATE)
      2. Check stock >= 1   (passes for ALL concurrent users if stock=10)
      3. sleep(0.1)         ← 100ms window — all 50 users pile up HERE
      4. F('stock') - 1     ← SQL decrement, each transaction commits serially
      5. stock = 10-50 = -40 ← NEGATIVE → overselling PROVEN

    Compare with /api/orders/checkout/ which uses SELECT FOR UPDATE:
      → only 10 orders succeed, stock = 0 exactly, never negative.
    """
    import time
    from django.db.models import F
    from apps.products.models import Product

    if request.user.is_staff:
        return Response({"error": "Admins cannot place orders."}, status=status.HTTP_403_FORBIDDEN)

    product_id = request.data.get("product_id")
    if not product_id:
        return Response({"error": "product_id required."}, status=status.HTTP_400_BAD_REQUEST)

    # ── Step 1: Read stock — NO lock ──────────────────────────────────────────
    try:
        product = Product.objects.get(id=product_id, is_active=True)
    except Product.DoesNotExist:
        return Response({"error": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

    stock_snapshot = product.stock   # snapshot — may already be stale!

    # ── Step 2: NO CHECK — this is the vulnerability! ────────────────────────
    # A real checkout would check: if stock_snapshot < 1: raise error
    # Here we SKIP that check to show pure overselling behaviour.
    # Every single concurrent user proceeds regardless of snapshot value.

    # ── Step 3: Intentional delay — race window ───────────────────────────────
    # All concurrent users pile up here simultaneously.
    # None has committed an UPDATE yet — all are sleeping at the same time.
    time.sleep(0.10)   # 100ms — all concurrent users are simultaneously here

    # ── Step 4: Decrement with SQL F() expression (NO lock) ──────────────────
    # F('stock') → SQL: UPDATE SET stock = stock - 1
    # PostgreSQL serialises the UPDATEs at row level, but the CHECK was already
    # done against a stale snapshot → stock will go NEGATIVE
    Product.objects.filter(id=product_id).update(stock=F("stock") - 1)

    # Read the ACTUAL stock after decrement (may be negative)
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


# ══════════════════════════════════════════════════════════════════════════════
# ⚠ DEMO ONLY — Synchronous Checkout (Requirement 3 — BEFORE solution)
# ══════════════════════════════════════════════════════════════════════════════
# Purpose:
#   Simulate the before-solution version where email sending happens
#   SYNCHRONOUSLY inside the HTTP request — the user must wait for the
#   full email delay before receiving a response.
#
# Behavior:
#   1. Creates the order using the same production service (create_order_from_cart)
#   2. Simulates synchronous email sending with time.sleep(2) + a log message
#   3. Returns total elapsed time in the response body for easy comparison
#
# Does NOT modify checkout_view in any way.
# Does NOT send a real email (uses console backend / sleep simulation only).
#
# Compare:
#   POST /api/orders/checkout-sync/  ← this demo: response after ~2s+
#   POST /api/orders/checkout/       ← real solution: response in <300ms
#
# To REMOVE: delete this function + its URL pattern in urls.py.
# ══════════════════════════════════════════════════════════════════════════════

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def checkout_sync_demo_view(request):
    """
    POST /api/orders/checkout-sync/

    ⚠ DEMO ONLY — Simulates synchronous email in the checkout path.
    The user waits for the full email delay before getting a response.
    """
    import time as _time

    if request.user.is_staff:
        return Response(
            {"error": "Admins cannot place orders."},
            status=status.HTTP_403_FORBIDDEN,
        )

    started = _time.time()

    # ── Step 1: Create order (same production service, unchanged) ─────────────
    try:
        order = services.create_order_from_cart(request.user)
    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)

    order_elapsed = round(_time.time() - started, 3)

    # ── Step 2: ⚠ Synchronous "email sending" — blocks the HTTP response ──────
    # In the before-solution, send_mail() is called directly here.
    # If email takes 2 seconds, the user waits 2 extra seconds.
    # If email fails, the user gets an error even though the order succeeded.
    import logging as _logging
    _log = _logging.getLogger(__name__)
    _log.warning(
        "[SYNC-EMAIL] ⚠ DEMO — Sending confirmation email SYNCHRONOUSLY "
        "for Order #%s — blocking HTTP response for 2s ...",
        order.id,
    )

    _time.sleep(2)   # ← simulates real SMTP latency (2 seconds)

    _log.warning(
        "[SYNC-EMAIL] ⚠ DEMO — Email 'sent' synchronously for Order #%s "
        "(user waited the full delay before receiving HTTP 201)",
        order.id,
    )

    total_elapsed = round(_time.time() - started, 3)

    # ── Step 3: Return response — only after email is "done" ─────────────────
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
    