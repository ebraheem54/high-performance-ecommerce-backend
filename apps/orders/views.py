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

    try:
        from notifications.tasks import send_order_confirmation_notification
        send_order_confirmation_notification.delay(order.id, request.user.id)
    except ImportError:
        pass

    return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


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

    return Response(OrderSerializer(order).data)


# ─────────────────────────────────────────────
# Admin-only endpoints
# ─────────────────────────────────────────────
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
