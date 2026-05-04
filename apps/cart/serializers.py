"""
Serializers for cart API.
"""

from rest_framework import serializers
from apps.cart.models import CartItem


class CartItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_price = serializers.DecimalField(
        source="product.price", max_digits=10, decimal_places=2, read_only=True
    )
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            "id", "product", "product_name", "product_price",
            "quantity", "subtotal", "added_at",
        ]
        read_only_fields = ["id", "product_name", "product_price", "subtotal", "added_at"]

    def get_subtotal(self, obj):
        return obj.product.price * obj.quantity


class AddToCartSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)
