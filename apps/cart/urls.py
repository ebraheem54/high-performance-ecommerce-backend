"""
URL mappings for the cart API.
"""

from django.urls import path
from apps.cart import views

app_name = "cart"

urlpatterns = [
    path("", views.cart_view, name="cart"),
    path("add/", views.add_to_cart_view, name="cart-add"),
    path("<int:product_id>/remove/", views.remove_from_cart_view, name="cart-remove"),
    path("clear/", views.clear_cart_view, name="cart-clear"),
]
