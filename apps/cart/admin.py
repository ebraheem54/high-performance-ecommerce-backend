from django.contrib import admin
from cart.models import CartItem


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "product", "quantity", "added_at"]
    search_fields = ["user__email", "product__name"]
    readonly_fields = ["added_at", "updated_at"]
