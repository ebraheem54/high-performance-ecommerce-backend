"""
URL mappings for the products API.
"""

from django.urls import path
from products import views

app_name = "products"

urlpatterns = [
    path("", views.ProductListView.as_view(), name="product-list"),
    path("<int:pk>/", views.ProductDetailView.as_view(), name="product-detail"),
    path("<int:product_id>/restock/", views.restock_view, name="product-restock"),
    path("<int:product_id>/reviews/", views.ProductReviewListView.as_view(), name="product-reviews"),
    path("<int:product_id>/inventory-logs/", views.InventoryLogListView.as_view(), name="inventory-logs"),
]
