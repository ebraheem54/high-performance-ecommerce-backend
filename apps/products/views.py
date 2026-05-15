"""
API endpoints for products.
Permissions:
  - Customer (is_staff=False): read-only (list, detail, reviews)
  - Admin    (is_staff=True) : full CRUD + restock + inventory logs
"""

from __future__ import annotations

from typing import Any

from django.core.cache import cache
from django.db.models import QuerySet
from rest_framework import generics
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import BasePermission
from rest_framework.permissions import (
    IsAuthenticated,
    IsAdminUser,
)
from rest_framework.request import Request
from rest_framework.response import Response

from apps.products import services
from apps.products.models import Product, Review
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
        serializer.save(
            user=self.request.user,
            product_id=self.kwargs["product_id"],
        )


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
