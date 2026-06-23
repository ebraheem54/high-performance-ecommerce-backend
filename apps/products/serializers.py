"""
Serializers for products API.
"""

from rest_framework import serializers
from apps.products.models import Product, InventoryLog, Review


class ProductSerializer(serializers.ModelSerializer):
    """Read/write serializer for Product."""

    class Meta:
        model = Product
        fields = ["id", "name", "description", "price", "stock", "is_active", "created_at"]
        read_only_fields = ["id", "stock", "created_at"]


class ProductPublicCacheSerializer(serializers.ModelSerializer):
    """
    Public product serializer used for cached read endpoints.

    Stock is intentionally excluded because it changes frequently and should be
    read from the database in checkout/reservation flows, not from Redis cache.
    """

    class Meta:
        model = Product
        fields = ["id", "name", "description", "price", "is_active", "created_at"]
        read_only_fields = fields


class ProductDetailSerializer(ProductSerializer):
    """Detailed product view including stock version (for admin/debug)."""

    class Meta(ProductSerializer.Meta):
        fields = ProductSerializer.Meta.fields + ["version", "updated_at"]


class InventoryLogSerializer(serializers.ModelSerializer):
    """Read-only serializer for inventory audit logs."""

    class Meta:
        model = InventoryLog
        fields = ["id", "product", "quantity_change", "reason", "note", "created_at"]
        read_only_fields = fields


class ReviewSerializer(serializers.ModelSerializer):
    """Serializer for product reviews."""

    user_email = serializers.CharField(source="user.email", read_only=True)

    class Meta:
        model = Review
        fields = ["id", "product", "user_email", "order", "rating", "comment", "created_at"]
        read_only_fields = ["id", "user_email", "created_at"]

    def validate_rating(self, value):
        if not (1 <= value <= 5):
            raise serializers.ValidationError("Rating must be between 1 and 5.")
        return value
