"""
URL mappings for the orders API.
"""

from django.urls import path
from apps.orders import views

app_name = "orders"

urlpatterns = [
    # Customer + Admin
    path("",                          views.MyOrderListView.as_view(),    name="order-list"),
    path("<int:pk>/",                 views.MyOrderDetailView.as_view(),  name="order-detail"),
    path("checkout/",                 views.checkout_view,                name="checkout"),
    path("<int:order_id>/cancel-unsafe/", views.cancel_order_unsafe_view, name="order-cancel-unsafe"),
    path("<int:order_id>/cancel/",    views.cancel_order_view,            name="order-cancel"),
    path("<int:order_id>/process-payment-unsafe/", views.process_payment_unsafe_view, name="order-process-payment-unsafe"),
    path("<int:order_id>/process-payment/", views.process_payment_view,    name="order-process-payment"),
    path("race-demo/",                views.race_demo_view,               name="race-demo"),
    path("unsafe-stock-checkout/",    views.race_demo_view,               name="unsafe-stock-checkout"),
    # Admin only
    path("<int:order_id>/status/",    views.update_order_status_view,     name="order-status"),

    path("checkout-sync/",            views.checkout_sync_demo_view,      name="checkout-sync"),

    path("wallet/balance/",           views.wallet_balance_view,          name="wallet-balance"),
    path("checkout-wallet-sync/",     views.checkout_wallet_sync_view,    name="checkout-wallet-sync"),
    path("blocking-wallet-checkout/", views.checkout_wallet_sync_view,    name="blocking-wallet-checkout"),
    path("checkout-wallet-async/",    views.checkout_wallet_async_view,   name="checkout-wallet-async"),
]
