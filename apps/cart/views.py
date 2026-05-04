"""
API endpoints for cart management.

Permissions:
  - Customer only — admins have no cart
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cart import services
from cart.serializers import CartItemSerializer, AddToCartSerializer


def _block_admin(request):
    """Return 403 response if requester is an admin, else None."""
    if request.user.is_staff:
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
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
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
        return Response({"error": "Item not found in cart."}, status=status.HTTP_404_NOT_FOUND)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def clear_cart_view(request):
    """DELETE /api/cart/clear/"""
    err = _block_admin(request)
    if err:
        return err
    count = services.clear_cart(request.user)
    return Response({"deleted": count})
