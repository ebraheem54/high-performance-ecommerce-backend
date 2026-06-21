"""
API endpoints for products.
Permissions:
  - Customer (is_staff=False): read-only (list, detail, reviews)
  - Admin    (is_staff=True) : full CRUD + restock + inventory logs
"""

from __future__ import annotations

import time
from typing import Any

from django.db import IntegrityError
from django.db.models import Avg, Count, QuerySet, Sum
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
from apps.products.cache_utils import (
    PRODUCT_CACHE_TTL,
    PRODUCT_LIST_CACHE_KEY,
    REQ7_PRODUCT_LIST_LOCK_KEY,
    REQ7_TOP_SELLING_LOCK_KEY,
    TOP_SELLING_PRODUCTS_CACHE_KEY,
    get_cached,
    get_or_set_cache_with_distributed_lock,
    invalidate_product_read_caches,
    invalidate_rating_summary_cache,
    product_detail_cache_key,
    product_rating_summary_cache_key,
    set_cached,
    should_bypass_cache,
    touches_manual_cached_fields,
)
from apps.products.models import Product, Review, OrderLock
from apps.products.serializers import (
    ProductSerializer,
    ProductPublicCacheSerializer,
    ProductDetailSerializer,
    InventoryLogSerializer,
    ReviewSerializer,
)

TOP_SELLING_PRODUCTS_LIMIT = 10
REQ7_MODES = {"before", "after"}


def _req7_mode(request: Request) -> str | None:
    mode = request.query_params.get("req7_mode", "").strip().lower()
    return mode if mode in REQ7_MODES else None


def _req7_delay_seconds(request: Request) -> float:
    try:
        delay_ms = int(request.query_params.get("req7_delay_ms", 200))
    except (TypeError, ValueError):
        delay_ms = 200
    return max(0, min(delay_ms, 2_000)) / 1000


def _with_req7_metadata(data: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
    return {"data": data, "_req7": metadata}


def _req7_response(data: list[dict[str, Any]], metadata: dict[str, Any]) -> Response:
    response = Response(_with_req7_metadata(data, metadata))
    response["X-Req7-Cache-Status"] = str(metadata.get("cache_status", "UNKNOWN"))
    response["X-Req7-DB-Query"] = "1" if metadata.get("db_query_executed") else "0"
    response["X-Req7-Lock-Acquired"] = "1" if metadata.get("lock_acquired") else "0"
    response["X-Req7-Served-After-Wait"] = "1" if metadata.get("served_after_wait") else "0"
    response["X-Req7-Fallback"] = "1" if metadata.get("fallback_used") else "0"
    response["X-Req7-Waited-Ms"] = str(metadata.get("waited_ms", 0))
    return response


def _build_top_selling_products(delay_seconds: float = 0) -> list[dict[str, Any]]:
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    from apps.orders.models import Order, OrderItem

    completed_statuses = [
        Order.Status.CONFIRMED,
        Order.Status.PROCESSING,
        Order.Status.SHIPPED,
        Order.Status.DELIVERED,
    ]

    top_rows = list(
        OrderItem.objects
        .filter(order__status__in=completed_statuses, product__is_active=True)
        .values("product_id")
        .annotate(total_sold=Sum("quantity"))
        .order_by("-total_sold")[:TOP_SELLING_PRODUCTS_LIMIT]
    )

    sales_by_product_id = {
        row["product_id"]: row["total_sold"] or 0
        for row in top_rows
    }

    products_by_id = {
        product.id: product
        for product in Product.objects.filter(id__in=sales_by_product_id.keys(), is_active=True)
    }

    data = []
    for product_id, total_sold in sales_by_product_id.items():
        product = products_by_id.get(product_id)
        if not product:
            continue
        serialized = dict(ProductPublicCacheSerializer(product).data)
        serialized["total_sold"] = int(total_sold)
        data.append(serialized)
    return data


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

    def _build_product_list_payload(self, delay_seconds: float = 0) -> Any:
        if delay_seconds > 0:
            time.sleep(delay_seconds)

        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = ProductPublicCacheSerializer(page, many=True)
            return self.get_paginated_response(serializer.data).data

        serializer = ProductPublicCacheSerializer(queryset, many=True)
        return serializer.data

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """
        Return the product list from Redis when available.

        Uses ProductPublicCacheSerializer so stock is not stored in Redis.
        """
        req7_mode = _req7_mode(request)
        if req7_mode:
            delay_seconds = _req7_delay_seconds(request)
            metadata: dict[str, Any] = {
                "mode": req7_mode,
                "cache_key": PRODUCT_LIST_CACHE_KEY,
                "cache_hit": False,
                "lock_acquired": False,
                "db_query_executed": False,
                "served_from_cache": False,
                "served_after_wait": False,
                "fallback_used": False,
                "cache_status": "MISS",
                "simulated_build_delay_ms": int(delay_seconds * 1000),
            }

            if req7_mode == "before":
                cached = get_cached(PRODUCT_LIST_CACHE_KEY)
                if cached is not None:
                    metadata["cache_hit"] = True
                    metadata["served_from_cache"] = True
                    metadata["cache_status"] = "HIT"
                    return _req7_response(cached, metadata)

                data = self._build_product_list_payload(delay_seconds=delay_seconds)
                metadata["db_query_executed"] = True
                metadata["cache_status"] = "MISS_DB_REBUILD_UNSAFE"
                set_cached(PRODUCT_LIST_CACHE_KEY, data, PRODUCT_CACHE_TTL)
                return _req7_response(data, metadata)

            data, lock_metadata = get_or_set_cache_with_distributed_lock(
                PRODUCT_LIST_CACHE_KEY,
                lambda: self._build_product_list_payload(delay_seconds=delay_seconds),
                ttl=PRODUCT_CACHE_TTL,
                lock_key=REQ7_PRODUCT_LIST_LOCK_KEY,
            )
            lock_metadata["mode"] = req7_mode
            lock_metadata["simulated_build_delay_ms"] = int(delay_seconds * 1000)
            return _req7_response(data, lock_metadata)

        bypass_cache = should_bypass_cache(request)

        if not bypass_cache:
            cached = get_cached(PRODUCT_LIST_CACHE_KEY)
            if cached is not None:
                return Response(cached)

        response = Response(self._build_product_list_payload())

        if not bypass_cache:
            set_cached(PRODUCT_LIST_CACHE_KEY, response.data, PRODUCT_CACHE_TTL)
        return response

    def perform_create(self, serializer: Any) -> None:
        product = serializer.save()
        invalidate_product_read_caches(product.id)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def top_selling_products_view(request: Request) -> Response:
    """
    GET /api/products/top-selling/

    Requirement 6 — Distributed Caching:
    A strong cache candidate because it performs aggregation over OrderItem.
    Returns the top 10 active products by total sold quantity.
    Stock is intentionally excluded from the cached payload.
    """
    req7_mode = _req7_mode(request)
    if req7_mode:
        delay_seconds = _req7_delay_seconds(request)
        metadata: dict[str, Any] = {
            "mode": req7_mode,
            "cache_key": TOP_SELLING_PRODUCTS_CACHE_KEY,
            "cache_hit": False,
            "lock_acquired": False,
            "db_query_executed": False,
            "served_from_cache": False,
            "served_after_wait": False,
            "fallback_used": False,
            "cache_status": "MISS",
            "simulated_build_delay_ms": int(delay_seconds * 1000),
        }

        if req7_mode == "before":
            cached = get_cached(TOP_SELLING_PRODUCTS_CACHE_KEY)
            if cached is not None:
                metadata["cache_hit"] = True
                metadata["served_from_cache"] = True
                metadata["cache_status"] = "HIT"
                return _req7_response(cached, metadata)

            data = _build_top_selling_products(delay_seconds=delay_seconds)
            metadata["db_query_executed"] = True
            metadata["cache_status"] = "MISS_DB_REBUILD_UNSAFE"
            set_cached(TOP_SELLING_PRODUCTS_CACHE_KEY, data, PRODUCT_CACHE_TTL)
            return _req7_response(data, metadata)

        data, lock_metadata = get_or_set_cache_with_distributed_lock(
            TOP_SELLING_PRODUCTS_CACHE_KEY,
            lambda: _build_top_selling_products(delay_seconds=delay_seconds),
            ttl=PRODUCT_CACHE_TTL,
            lock_key=REQ7_TOP_SELLING_LOCK_KEY,
        )
        lock_metadata["mode"] = req7_mode
        lock_metadata["simulated_build_delay_ms"] = int(delay_seconds * 1000)
        return _req7_response(data, lock_metadata)

    bypass_cache = should_bypass_cache(request)

    if not bypass_cache:
        cached = get_cached(TOP_SELLING_PRODUCTS_CACHE_KEY)
        if cached is not None:
            return Response(cached)

    data = _build_top_selling_products()

    if not bypass_cache:
        set_cached(TOP_SELLING_PRODUCTS_CACHE_KEY, data, PRODUCT_CACHE_TTL)
    return Response(data)


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
            return ProductDetailSerializer  # admin sees stock + version + updated_at
        return ProductSerializer

    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """
        Return product detail from Redis for customers only.

        Customer cached payload excludes stock. Admin detail is always read fresh
        from the database because it includes stock/debug fields.
        """
        product_id = int(kwargs.get(self.lookup_url_kwarg or self.lookup_field))

        if request.user and request.user.is_staff:
            instance = self.get_object()
            return Response(ProductDetailSerializer(instance).data)

        bypass_cache = should_bypass_cache(request)
        cache_key = product_detail_cache_key(product_id, is_staff=False)

        if not bypass_cache:
            cached = get_cached(cache_key)
            if cached is not None:
                return Response(cached)

        instance = self.get_object()
        serializer = ProductPublicCacheSerializer(instance)
        if not bypass_cache:
            set_cached(cache_key, serializer.data, PRODUCT_CACHE_TTL)
        return Response(serializer.data)

    def perform_update(self, serializer: Any) -> None:
        should_invalidate = touches_manual_cached_fields(self.request.data)
        product = serializer.save()
        if should_invalidate:
            invalidate_product_read_caches(
                product.id,
                include_rating="is_active" in self.request.data,
            )

    def perform_destroy(self, instance: Product) -> None:
        product_id = instance.id
        instance.delete()
        invalidate_product_read_caches(product_id, include_rating=True)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def restock_view(request: Request, product_id: int) -> Response:
    """
    POST /api/products/<id>/restock/       — admin only
    Body: { "quantity": 50, "note": "New shipment" }

    Stock is not cached in public product read endpoints, so restock does not
    need to invalidate product list/detail cache.
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


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def product_rating_summary_view(request: Request, product_id: int) -> Response:
    """
    GET /api/products/<id>/rating-summary/

    Requirement 6 — Distributed Caching:
    Cache only numeric review summary, not review comments.
    This is safer and more stable than caching user-generated comments.
    """
    bypass_cache = should_bypass_cache(request)
    cache_key = product_rating_summary_cache_key(product_id)

    if not bypass_cache:
        cached = get_cached(cache_key)
        if cached is not None:
            return Response(cached)

    if not Product.objects.filter(id=product_id, is_active=True).exists():
        return Response({"error": "Product not found."}, status=404)

    summary = Review.objects.filter(product_id=product_id).aggregate(
        average_rating=Avg("rating"),
        ratings_count=Count("id"),
    )

    average_rating = summary["average_rating"]
    data = {
        "product_id": product_id,
        "average_rating": round(float(average_rating), 2) if average_rating is not None else None,
        "ratings_count": summary["ratings_count"],
    }

    if not bypass_cache:
        set_cached(cache_key, data, PRODUCT_CACHE_TTL)
    return Response(data)


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
        product_id = self.kwargs["product_id"]
        try:
            serializer.save(
                user=self.request.user,
                product_id=product_id,
            )
            invalidate_rating_summary_cache(product_id)
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
