"""
API endpoints for products.
Permissions:
  - Customer (is_staff=False): read-only (list, detail, reviews)
  - Admin    (is_staff=True) : full CRUD + restock + inventory logs
"""

from django.core.cache import cache
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import (
    IsAuthenticated,
    IsAdminUser,
    IsAuthenticatedOrReadOnly,
)
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

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAdminUser()]  # only admin can create
        return [IsAuthenticated()]  # customers can list

    def get_queryset(self):
        return services.get_active_products()

    def list(self, request, *args, **kwargs):
        """Return cached product list if available (Redis cache)."""
        cached = cache.get(PRODUCT_LIST_CACHE_KEY)
        if cached:
            return Response(cached)

        response = super().list(request, *args, **kwargs)
        cache.set(PRODUCT_LIST_CACHE_KEY, response.data, PRODUCT_LIST_CACHE_TTL)
        return response

    def perform_create(self, serializer):
        serializer.save()
        cache.delete(PRODUCT_LIST_CACHE_KEY)


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/products/<id>/  — customer: view product
    PATCH  /api/products/<id>/  — admin: update product
    DELETE /api/products/<id>/  — admin: delete product
    """

    queryset = Product.objects.filter(is_active=True)

    def get_permissions(self):
        if self.request.method in ("PATCH", "PUT", "DELETE"):
            return [IsAdminUser()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.request.user and self.request.user.is_staff:
            return ProductDetailSerializer  # admin sees version + updated_at
        return ProductSerializer  # customer sees basic info

    def perform_update(self, serializer):
        serializer.save()
        cache.delete(PRODUCT_LIST_CACHE_KEY)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def restock_view(request, product_id):
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

    def get_queryset(self):
        return Review.objects.filter(product_id=self.kwargs["product_id"])

    def perform_create(self, serializer):
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


    def get_queryset(self):
        return services.get_product_by_id(
            self.kwargs["product_id"]
        ).inventory_logs.all()
