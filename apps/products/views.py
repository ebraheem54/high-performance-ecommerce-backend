"""
API endpoints for products.
Permissions:
  - Customer (is_staff=False): read-only (list, detail, reviews)
  - Admin    (is_staff=True) : full CRUD + restock + inventory logs
"""

from __future__ import annotations

from typing import Any

from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework import generics
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.permissions import (
    IsAuthenticated,
    IsAdminUser,
)
from rest_framework.request import Request
from rest_framework.response import Response

from apps.products import services
from apps.products.models import Product, Review, OrderLock
from apps.products.serializers import (
    ProductSerializer,
    ProductDetailSerializer,
    InventoryLogSerializer,
    ReviewSerializer,
)

PRODUCT_LIST_CACHE_KEY = "product_list"
PRODUCT_LIST_CACHE_TTL = 60 * 5  # 5 minutes


class ProductListView(generics.ListCreateAPIView):
    """
    GET  /api/products/   — anyone (authenticated) can list products
    POST /api/products/   — admin only: create a product
    """

    serializer_class = ProductSerializer

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "POST":
            return [IsAdminUser()]  # only admin can create
        return [IsAuthenticated()]  # customers can list

    def get_queryset(self) -> QuerySet[Product]:
        return services.get_active_products()

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Return the product list from Redis when available."""
        cached = cache.get(PRODUCT_LIST_CACHE_KEY)
        if cached is not None:
            return Response(cached)

        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
        else:
            serializer = self.get_serializer(queryset, many=True)
            response = Response(serializer.data)

        cache.set(PRODUCT_LIST_CACHE_KEY, response.data, PRODUCT_LIST_CACHE_TTL)
        return response

    def perform_create(self, serializer: Any) -> None:
        serializer.save()
        cache.delete(PRODUCT_LIST_CACHE_KEY)


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/products/<id>/  — customer: view product
    PATCH  /api/products/<id>/  — admin: update product
    DELETE /api/products/<id>/  — admin: delete product
    """

    queryset = Product.objects.filter(is_active=True)

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method in ("PATCH", "PUT", "DELETE"):
            return [IsAdminUser()]
        return [IsAuthenticated()]

    def get_serializer_class(self) -> type[ProductSerializer]:
        if self.request.user and self.request.user.is_staff:
            return ProductDetailSerializer  # admin sees version + updated_at
        return ProductSerializer  # customer sees basic info

    def perform_update(self, serializer: Any) -> None:
        serializer.save()
        cache.delete(PRODUCT_LIST_CACHE_KEY)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def restock_view(request: Request, product_id: int) -> Response:
    """
    POST /api/products/<id>/restock/       — admin only
    Body: { "quantity": 50, "note": "New shipment" }
    """
    quantity = request.data.get("quantity")
    note = request.data.get("note", "")
    if not quantity or int(quantity) <= 0:
        return Response({"error": "quantity must be a positive integer."}, status=400)

    product = services.restock_product(product_id, int(quantity), note)
    return Response(ProductDetailSerializer(product).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def reserve_product_view(request: Request, product_id: int) -> Response:
    """
    POST /api/products/<id>/reserve/
    Body: { "quantity": 1, "lock_minutes": 10 }

    Requirement 1 demo endpoint:
    exposes create_order_lock(), which locks the Product row with
    select_for_update() while creating a temporary reservation.
    """
    if request.user.is_staff:
        return Response({"error": "Admins cannot reserve products."}, status=403)

    try:
        quantity = int(request.data.get("quantity", 1))
        lock_minutes = int(request.data.get("lock_minutes", 10))
        if quantity <= 0 or lock_minutes <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return Response(
            {"error": "quantity and lock_minutes must be positive integers."},
            status=400,
        )

    try:
        lock = services.create_order_lock(product_id, request.user.id, quantity, lock_minutes)
    except Product.DoesNotExist:
        return Response({"error": "Product not found."}, status=404)
    except ValueError as exc:
        return Response({"error": str(exc)}, status=400)

    return Response(
        {
            "lock_id": lock.id,
            "product_id": product_id,
            "quantity": lock.quantity,
            "expires_at": lock.expires_at,
            "_demo": "Pessimistic product reservation via select_for_update().",
        },
        status=201,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def reserve_product_unsafe_view(request: Request, product_id: int) -> Response:
    """
    POST /api/products/<id>/reserve-unsafe/

    DEMO ONLY — Requirement 1 BEFORE state.
    Intentionally creates reservations without select_for_update().
    Concurrent users can over-reserve the same product because each request
    reads stale stock/reservation state before creating its lock.
    """
    if request.user.is_staff:
        return Response({"error": "Admins cannot reserve products."}, status=403)

    try:
        quantity = int(request.data.get("quantity", 1))
        lock_minutes = int(request.data.get("lock_minutes", 10))
        if quantity <= 0 or lock_minutes <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return Response(
            {"error": "quantity and lock_minutes must be positive integers."},
            status=400,
        )

    try:
        product = Product.objects.get(id=product_id, is_active=True)
    except Product.DoesNotExist:
        return Response({"error": "Product not found."}, status=404)

    active_reserved = sum(
        lock.quantity
        for lock in OrderLock.objects.filter(
            product_id=product_id,
            expires_at__gt=timezone.now(),
        )
    )

    from datetime import timedelta
    import time as _time

    _time.sleep(0.10)
    lock = OrderLock.objects.create(
        product=product,
        user=request.user,
        quantity=quantity,
        expires_at=timezone.now() + timedelta(minutes=lock_minutes),
    )

    actual_reserved = sum(
        current_lock.quantity
        for current_lock in OrderLock.objects.filter(
            product_id=product_id,
            expires_at__gt=timezone.now(),
        )
    )

    return Response(
        {
            "lock_id": lock.id,
            "product_id": product_id,
            "quantity": lock.quantity,
            "stock": product.stock,
            "snapshot_reserved": active_reserved,
            "actual_reserved": actual_reserved,
            "over_reserved": actual_reserved > product.stock,
            "warning": "NO LOCKING — unsafe reservation demo endpoint",
        },
        status=201,
    )


class ProductReviewListView(generics.ListCreateAPIView):
    """
    GET  /api/products/<id>/reviews/  — any authenticated user
    POST /api/products/<id>/reviews/  — customer who bought the product
    """

    serializer_class = ReviewSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self) -> QuerySet[Review]:
        return Review.objects.filter(product_id=self.kwargs["product_id"])

    def perform_create(self, serializer: Any) -> None:
        try:
            serializer.save(
                user=self.request.user,
                product_id=self.kwargs["product_id"],
            )
        except IntegrityError as exc:
            raise ValidationError({
                "error": "You have already reviewed this product. Each user can only submit one review per product."
            }) from exc


class InventoryLogListView(generics.ListAPIView):
    """
    GET /api/products/<id>/inventory-logs/  — admin only
    """
    serializer_class   = InventoryLogSerializer
    permission_classes = [IsAdminUser]


    def get_queryset(self) -> QuerySet[Any]:
        return services.get_product_by_id(
            self.kwargs["product_id"]
        ).inventory_logs.all()
