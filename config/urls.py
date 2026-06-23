"""
Central URL router — includes all app URL configurations.
"""

from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from apps.core.views import metrics_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("metrics", metrics_view, name="metrics"),

    # API docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),

    # App routes
    path("api/users/", include("apps.users.urls", namespace="users")),
    path("api/products/", include("apps.products.urls", namespace="products")),
    path("api/orders/", include("apps.orders.urls", namespace="orders")),
    path("api/cart/", include("apps.cart.urls", namespace="cart")),
    path("api/core/", include("apps.core.urls", namespace="core")),
    # Uncomment after creating notifications and reports apps locally:
    # path("api/notifications/", include("notifications.urls", namespace="notifications")),
    # path("api/reports/", include("reports.urls", namespace="reports")),
]
