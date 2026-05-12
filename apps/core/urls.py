"""
URL mappings for the core API.
"""

from django.urls import path
from apps.core import views

app_name = "core"

urlpatterns = [
    # Batch Processing (Requirement 4) — manual trigger for demo/testing
    path("trigger-batch/", views.trigger_batch_view, name="trigger-batch"),
]
