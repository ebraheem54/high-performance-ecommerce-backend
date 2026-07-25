"""
URL mappings for the products API.
"""

from django.urls import path
from apps.products import views

app_name = "products"

urlpatterns = [
    path("", views.ProductListView.as_view(), name="product-list"),
    path("top-selling/", views.top_selling_products_view, name="product-top-selling"),
    path("<int:pk>/", views.ProductDetailView.as_view(), name="product-detail"),
    path("<int:product_id>/rating-summary/", views.product_rating_summary_view, name="product-rating-summary"),
    path("<int:product_id>/restock/", views.restock_view, name="product-restock"),
    path("<int:product_id>/reserve-unsafe/", views.reserve_product_unsafe_view, name="product-reserve-unsafe"),
    path("<int:product_id>/reserve/", views.reserve_product_view, name="product-reserve"),
    path("<int:product_id>/reviews/", views.ProductReviewListView.as_view(), name="product-reviews"),
    path("<int:product_id>/inventory-logs/", views.InventoryLogListView.as_view(), name="inventory-logs"),
]
