"""
Core API views.
"""

import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger(__name__)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def trigger_batch_view(request):
    """
    POST /api/core/trigger-batch/  — admin only

    Batch Processing (Requirement 4):
    Manually trigger run_daily_sales_batch_task via HTTP.

    Why this endpoint?
      The task runs automatically every night at 01:00 via Celery Beat.
      This endpoint allows triggering it on-demand during demos or testing
      without waiting for the scheduled time.

    Response: 202 Accepted — task queued in Celery, runs asynchronously.
    """
    try:
        from apps.core.tasks import run_daily_sales_batch_task
        result = run_daily_sales_batch_task.delay()
        logger.info("[BATCH] Manually triggered by admin: task_id=%s", result.id)
        return Response(
            {
                "message": "Batch processing task queued successfully.",
                "task_id": result.id,
                "info": (
                    "Task is running asynchronously in Celery worker. "
                    "Check Celery logs for: [BATCH] Chunk X/Y processed..."
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )
    except Exception as e:
        logger.error("[BATCH] Failed to trigger batch task: %s", e)
        return Response(
            {"error": f"Failed to queue batch task: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
