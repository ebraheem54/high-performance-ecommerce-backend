"""
URL mappings for the core API.
"""

from django.urls import path
from apps.core import views

app_name = "core"

urlpatterns = [
    # Batch Processing (Requirement 4) — manual trigger for demo/testing
    path("trigger-batch/",       views.trigger_batch_view,       name="trigger-batch"),

    # ── DEMO ONLY routes — safe to remove after report ────────────────────────
    # Requirement 2: capacity stress demo
    path("capacity-stress/",     views.capacity_stress_view,     name="capacity-stress"),
    # Requirement 4: naive (no-chunk) batch demo
    path("trigger-batch-naive/", views.trigger_batch_naive_view, name="trigger-batch-naive"),
]
