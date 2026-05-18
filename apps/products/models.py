
"""
Database models for products app.
Includes Product, InventoryLog, OrderLock, and Review.
"""

from django.conf import settings
from django.db import models


class Product(models.Model):
    """Product available in the store."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.IntegerField(default=0)

    # version field for Optimistic Locking — incremented on every stock update
    version = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["price"]),
        ]

    def __str__(self):
        return f"{self.name} (stock={self.stock})"


class InventoryLog(models.Model):
    """
    Audit log for every stock change on a product.
    Relation: PRODUCT → INVENTORY_LOG (1:N)
    """

    class Reason(models.TextChoices):
        PURCHASE = "PURCHASE", "Purchase"
        RESTOCK = "RESTOCK", "Restock"
        RETURN = "RETURN", "Return"
        ADJUSTMENT = "ADJUSTMENT", "Manual Adjustment"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="inventory_logs",
    )
    quantity_change = models.IntegerField()  # positive = added, negative = removed
    reason = models.CharField(max_length=20, choices=Reason.choices)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name}: {self.quantity_change} ({self.reason})"


class OrderLock(models.Model):
    """
    Temporary reservation lock on a product during checkout.
    Prevents race conditions when multiple users buy the same item.
    Relation: PRODUCT → ORDER_LOCK (1:N)
    """

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="order_locks",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="order_locks",
    )
    quantity = models.PositiveIntegerField()
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["product", "expires_at"]),
        ]

    def __str__(self):
        return f"Lock: {self.user} → {self.product.name} x{self.quantity}"


class Review(models.Model):
    """
    Product review. Only users who purchased the product can review it.
    Relations:
      - PRODUCT → REVIEW (1:N)
      - USER    → REVIEW (1:N)
      - ORDER   → REVIEW (1:N)  ← verifies the purchase happened
    """

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        related_name="reviews",
    )
    rating = models.PositiveSmallIntegerField()  # 1–5
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # One review per user per product
        unique_together = ("user", "product")

    def __str__(self):
        return f"{self.user.email} → {self.product.name} ({self.rating}★)"
