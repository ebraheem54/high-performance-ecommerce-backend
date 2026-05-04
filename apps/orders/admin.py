from django.contrib import admin
from apps.orders.models import Order, OrderItem, Payment


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ["product", "quantity", "unit_price"]


class PaymentInline(admin.StackedInline):
    model = Payment
    extra = 0
    readonly_fields = ["amount", "status", "method", "transaction_id", "created_at"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "status", "total_price", "created_at"]
    list_filter = ["status"]
    search_fields = ["user__email"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [OrderItemInline, PaymentInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["id", "order", "amount", "status", "method", "created_at"]
    list_filter = ["status", "method"]
    readonly_fields = ["created_at"]
