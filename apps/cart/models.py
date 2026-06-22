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
     Optimistic Locking:
      The `version` field is used to detect concurrent modifications.
      When two requests try to update the same cart item simultaneously,
      only the first one succeeds; the second gets a conflict error.
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
    # Incremented on every update to detect concurrent writes.
    # If two transactions read version=3 and both try to update,
    # only the first UPDATE WHERE version=3 succeeds (returns 1 row affected).
    # The second finds version=4 already and fails (returns 0 rows) → conflict.
    version = models.PositiveIntegerField(default=0)
    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One row per user per product
        unique_together = ("user", "product")

    def __str__(self):
        return f"{self.user.email} → {self.product.name} x{self.quantity}"
