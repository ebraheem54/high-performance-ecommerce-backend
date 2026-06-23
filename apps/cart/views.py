"""
API endpoints for cart management.

Permissions:
  - Customer only — admins have no cart
"""

import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.cart import services
from apps.cart.exceptions import ProductNotFoundError, OutOfStockError
from apps.cart.serializers import CartItemSerializer, AddToCartSerializer
from apps.core.logging_utils import log_user_error, log_user_event, log_user_warning
from apps.core.metrics import record_cart_action

logger = logging.getLogger(__name__)


def _log_cart_issue(
    request,
    event: str,
    response_status: int,
    reason: str,
    level: str = "warning",
    **fields,
) -> None:
    user_id = getattr(request.user, "id", None)
    log_message = (
        "cart_issue level=%s event=%s user=%s status=%s reason=%s fields=%s"
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


def _block_admin(request):
    """Return 403 response if requester is an admin, else None."""
    if request.user.is_staff:
        record_cart_action("admin_blocked", "rejected")
        _log_cart_issue(
            request,
            "cart.admin_blocked",
            status.HTTP_403_FORBIDDEN,
            "Admins do not have a shopping cart.",
            level="info",
        )
        return Response(
            {"error": "Admins do not have a shopping cart."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def cart_view(request):
    """GET /api/cart/ — customer's current cart."""
    err = _block_admin(request)
    if err:
        return err
    items = services.get_cart(request.user)
    total = sum(item.product.price * item.quantity for item in items)
    return Response({"items": CartItemSerializer(items, many=True).data, "total": total})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_to_cart_view(request):
    """POST /api/cart/add/   Body: { product_id, quantity }"""
    err = _block_admin(request)
    if err:
        return err
    serializer = AddToCartSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        item = services.add_to_cart(
            request.user,
            serializer.validated_data["product_id"],
            serializer.validated_data["quantity"],
        )
    except ProductNotFoundError as e:
        record_cart_action("add", "product_not_found")
        _log_cart_issue(
            request,
            "cart.add",
            status.HTTP_404_NOT_FOUND,
            str(e),
            level="info",
            product_id=serializer.validated_data["product_id"],
        )
        return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
    except OutOfStockError as e:
        record_cart_action("add", "out_of_stock")
        _log_cart_issue(
            request,
            "cart.add",
            status.HTTP_404_NOT_FOUND,
            str(e),
            level="warning",
            product_id=serializer.validated_data["product_id"],
        )
        return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        record_cart_action("add", "failed")
        _log_cart_issue(
            request,
            "cart.add",
            status.HTTP_400_BAD_REQUEST,
            str(e),
            level="warning",
            product_id=serializer.validated_data["product_id"],
        )
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    record_cart_action("add", "success")
    return Response(CartItemSerializer(item).data, status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def remove_from_cart_view(request, product_id):
    """DELETE /api/cart/<product_id>/remove/"""
    err = _block_admin(request)
    if err:
        return err
    removed = services.remove_from_cart(request.user, product_id)
    if not removed:
        record_cart_action("remove", "not_found")
        _log_cart_issue(
            request,
            "cart.remove",
            status.HTTP_404_NOT_FOUND,
            "Item not found in cart.",
            level="info",
            product_id=product_id,
        )
        return Response({"error": "Item not found in cart."}, status=status.HTTP_404_NOT_FOUND)
    record_cart_action("remove", "success")
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_cart_quantity_view(request, product_id):
    """
    PATCH /api/cart/<product_id>/quantity/
    Body: { "quantity": 3 }

    Requirement 1 demo endpoint:
    exposes update_cart_item_quantity(), which uses select_for_update()
    to serialize concurrent exact-quantity updates for the same cart item.
    """
    err = _block_admin(request)
    if err:
        return err

    try:
        quantity = int(request.data.get("quantity", 1))
    except (TypeError, ValueError):
        record_cart_action("quantity_update", "invalid_quantity")
        _log_cart_issue(
            request,
            "cart.quantity_update",
            status.HTTP_400_BAD_REQUEST,
            "quantity must be an integer.",
            level="warning",
            product_id=product_id,
        )
        return Response({"error": "quantity must be an integer."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        item = services.update_cart_item_quantity(request.user, product_id, quantity)
    except ValueError as exc:
        record_cart_action("quantity_update", "failed")
        _log_cart_issue(
            request,
            "cart.quantity_update",
            status.HTTP_404_NOT_FOUND,
            str(exc),
            level="warning",
            product_id=product_id,
            quantity=quantity,
        )
        return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)

    if item is None:
        record_cart_action("quantity_update", "deleted")
        return Response({"deleted": True}, status=status.HTTP_200_OK)

    record_cart_action("quantity_update", "success")
    return Response(CartItemSerializer(item).data, status=status.HTTP_200_OK)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def clear_cart_view(request):
    """DELETE /api/cart/clear/"""
    err = _block_admin(request)
    if err:
        return err
    count = services.clear_cart(request.user)
    record_cart_action("clear", "success")
    return Response({"deleted": count})
