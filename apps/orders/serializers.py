"""
Serializers for orders API.
"""

from rest_framework import serializers
from apps.orders.models import Order, OrderItem, Payment


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    subtotal = serializers.SerializerMethodField()
    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_name", "quantity", "unit_price", "subtotal"]
        read_only_fields = fields
    def get_subtotal(self, obj):
        return obj.unit_price * obj.quantity


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "amount", "status", "method", "transaction_id", "created_at"]
        read_only_fields = ["id", "amount", "created_at"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    payment = PaymentSerializer(read_only=True)

    class Meta:
        model = Order
        fields = ["id", "status", "total_price", "items", "payment", "created_at", "updated_at"]
        read_only_fields = fields


class OrderListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for the order list endpoint."""

    class Meta:
        model = Order
        fields = ["id", "status", "total_price", "created_at"]
        read_only_fields = fields
