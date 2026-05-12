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
    path("<int:order_id>/cancel/",    views.cancel_order_view,            name="order-cancel"),
      # Direct endpoint: no cart, no stock floor, 100ms sleep → stock goes negative
    path("race-demo/",                views.race_demo_view,               name="race-demo"),
    # Admin only
    path("<int:order_id>/status/",    views.update_order_status_view,     name="order-status"),
]
