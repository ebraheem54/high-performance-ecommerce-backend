from django.contrib import admin
from products.models import Product, InventoryLog, OrderLock, Review


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "price", "stock", "version", "is_active", "updated_at"]
    list_filter = ["is_active"]
    search_fields = ["name"]
    readonly_fields = ["version", "created_at", "updated_at"]


@admin.register(InventoryLog)
class InventoryLogAdmin(admin.ModelAdmin):
    list_display = ["id", "product", "quantity_change", "reason", "created_at"]
    list_filter = ["reason"]
    readonly_fields = ["created_at"]


@admin.register(OrderLock)
class OrderLockAdmin(admin.ModelAdmin):
    list_display = ["id", "product", "user", "quantity", "expires_at", "created_at"]
    readonly_fields = ["created_at"]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["id", "product", "user", "rating", "created_at"]
    list_filter = ["rating"]
    readonly_fields = ["created_at"]
