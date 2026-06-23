"""
URL mappings for the core API.
"""

from django.urls import path
from apps.core import views

app_name = "core"

urlpatterns = [
    path("trigger-batch/",       views.trigger_batch_view,       name="trigger-batch"),

    path("capacity-stress/",     views.CapacityStressView.as_view(), name="capacity-stress"),
    path("trigger-batch-naive/", views.trigger_batch_naive_view, name="trigger-batch-naive"),
]
