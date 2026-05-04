"""
Database models for cart app.
CartItem holds products a user intends to buy.
"""

from django.conf import settings
from django.db import models


class CartItem(models.Model):
    """
    A product in a user's shopping cart.
    Relations:
      - USER    → CART_ITEM (1:N)
      - PRODUCT → CART_ITEM (1:N)
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cart_items",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="cart_items",
    )
    quantity = models.PositiveIntegerField(default=1)
    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One row per user per product
        unique_together = ("user", "product")

    def __str__(self):
        return f"{self.user.email} → {self.product.name} x{self.quantity}"
