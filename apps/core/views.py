"""
Core API views.
"""

from __future__ import annotations

import logging
from threading import Lock
from typing import Any

from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response

logger = logging.getLogger(__name__)

CAPACITY_STRESS_DB_SAMPLE_EVERY = 20
_capacity_stress_request_count = 0
_capacity_stress_request_lock = Lock()


@api_view(["POST"])
@permission_classes([IsAdminUser])
def trigger_batch_view(request: Request) -> Response:
    """
    POST /api/core/trigger-batch/  — admin only

    Batch Processing (Requirement 4):
    Manually trigger run_daily_sales_batch_task via HTTP.

    Accepts optional query parameter for demo:
      ?chunk_size=10  →  uses 10 instead of the default CHUNK_SIZE=50
                         so you can show "Chunk 1/4" with fewer orders.
      (omit it)       →  uses the production default CHUNK_SIZE=50

    Response: 202 Accepted — task queued in Celery, runs asynchronously.
    """
    try:
        from apps.core.tasks import run_daily_sales_batch_task, CHUNK_SIZE

        # ── Optional demo parameter: override chunk_size for the screenshot ───
        # This does NOT change the global CHUNK_SIZE constant.
        # Passing chunk_size=10 with 33 orders → ceil(33/10) = 4 chunks.
        # Omitting it uses the production default (CHUNK_SIZE=50).
        raw = request.query_params.get("chunk_size") or request.data.get("chunk_size")
        demo_chunk_size = None
        if raw is not None:
            try:
                demo_chunk_size = int(raw)
                if demo_chunk_size <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return Response(
                    {"error": "chunk_size must be a positive integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        result = run_daily_sales_batch_task.apply_async(
            kwargs={"demo_chunk_size": demo_chunk_size} if demo_chunk_size else {}
        )

        logger.info(
            "[BATCH] Manually triggered by admin: task_id=%s chunk_size=%s",
            result.id,
            demo_chunk_size if demo_chunk_size else CHUNK_SIZE,
        )
        return Response(
            {
                "message"   : "Batch processing task queued successfully.",
                "task_id"   : result.id,
                "chunk_size": demo_chunk_size if demo_chunk_size else CHUNK_SIZE,
                "info"      : (
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


# ══════════════════════════════════════════════════════════════════════════════
# DEMO ONLY — Capacity Stress View (Requirement 2)
# ══════════════════════════════════════════════════════════════════════════════
# Purpose:
#   Lightweight Locust endpoint for Requirement 2. It avoids normal ORM work and
#   samples pg_stat_activity occasionally, then closes that measurement
#   connection so the demo endpoint does not inflate persistent DB connections.
#
# To REMOVE: delete this function + its URL pattern in urls.py.
# ══════════════════════════════════════════════════════════════════════════════

class CapacityStressView(generics.GenericAPIView):
    """
    POST /api/core/capacity-stress/  — admin only

    Lightweight capacity endpoint for Requirement 2.

    Connection count is sampled every CAPACITY_STRESS_DB_SAMPLE_EVERY requests,
    or forced with ?sample_db=1.
    """
    permission_classes = [IsAdminUser]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        import time as _time
        from django.db import connection

        started = _time.time()
        work_started = _time.time()
        work_units = sum(range(100))
        work_time = round(_time.time() - work_started, 3)

        global _capacity_stress_request_count
        with _capacity_stress_request_lock:
            _capacity_stress_request_count += 1
            request_count = _capacity_stress_request_count

        force_db_sample = request.query_params.get("sample_db") in {"1", "true", "yes"}
        should_sample_db = (
            force_db_sample
            or request_count % CAPACITY_STRESS_DB_SAMPLE_EVERY == 0
        )

        # Sampling pg_stat_activity every request adds measurable DB work under load.
        db_conn_count = None
        if should_sample_db:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database();"
                    )
                    db_conn_count = cursor.fetchone()[0]
            except Exception as exc:
                logger.warning(
                    "[CAPACITY-STRESS] Could not read pg_stat_activity: %s",
                    exc,
                )

        elapsed = round(_time.time() - started, 3)

        logger.debug(
            "[CAPACITY-STRESS] completed in %.3fs | work_units=%s | sampled_db=%s | "
            "open_db_connections=%s",
            elapsed,
            work_units,
            should_sample_db,
            db_conn_count,
        )

        # Authentication may touch the DB before this view runs. This endpoint is
        # a load-test helper, so close its request connection instead of keeping
        # it in the per-thread persistent pool.
        connection.close()

        return Response(
            {
                "_demo": "capacity stress endpoint for Requirement 2",
                "elapsed_s": elapsed,
                "app_work_time_s": work_time,
                "open_db_connections": db_conn_count,
                "db_connection_sampled": should_sample_db,
                "db_sample_every": CAPACITY_STRESS_DB_SAMPLE_EVERY,
                "notes": {
                    "query_strategy": "no ORM query during normal requests",
                    "connection_note": (
                        "This endpoint closes its request DB connection so it "
                        "does not inflate the persistent per-thread connection pool."
                    ),
                },
                "instructions": {
                    "before": "Set CONN_MAX_AGE=0, restart app containers, run Locust, capture pg_stat_activity.",
                    "after": "Set CONN_MAX_AGE=60, restart app containers, run Locust, capture pg_stat_activity.",
                    "force_sample": "POST /api/core/capacity-stress/?sample_db=1",
                    "query": "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database();",
                },
            },
            status=status.HTTP_200_OK,
        )


# ══════════════════════════════════════════════════════════════════════════════
# ⚠ DEMO ONLY — Trigger Naive Batch (Requirement 4 — BEFORE solution)
# ══════════════════════════════════════════════════════════════════════════════
# Purpose:
#   HTTP endpoint to manually trigger run_daily_sales_batch_naive_task.
#   Accepts optional {"days_back": 7} in the request body to widen the
#   date window when yesterday has 0 orders (e.g. fresh DB).
#
# To REMOVE: delete this function + its URL pattern in urls.py.
# ══════════════════════════════════════════════════════════════════════════════

@api_view(["POST"])
@permission_classes([IsAdminUser])
def trigger_batch_naive_view(request: Request) -> Response:
    """
    POST /api/core/trigger-batch-naive/  — admin only

    ⚠ DEMO ONLY — Triggers the naive (no-chunk) batch task for Requirement 4.

    Optional body: { "days_back": 7 }
      Default is 1 (yesterday). Use 7 to include all orders from the last week.

    Compare Celery logs:
      [BATCH-NAIVE] ⚠ Loaded ALL X orders into memory at once — NO CHUNKING
      vs
      [BATCH] Chunk 1/4 processed ...
      [BATCH] Chunk 2/4 processed ...
    """
    try:
        from apps.core.tasks import run_daily_sales_batch_naive_task

        try:
            days_back = int(request.data.get("days_back", 1))
            if days_back <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return Response(
                {"error": "days_back must be a positive integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result    = run_daily_sales_batch_naive_task.apply_async(
            kwargs={"days_back": days_back}
        )

        logger.warning(
            "[BATCH-NAIVE] ⚠ DEMO — Manually triggered naive task: "
            "task_id=%s days_back=%s",
            result.id, days_back,
        )
        return Response(
            {
                "message"  : "⚠ DEMO — Naive batch task queued (no chunking).",
                "task_id"  : result.id,
                "days_back": days_back,
                "info"     : (
                    "Watch Celery logs for [BATCH-NAIVE] prefix. "
                    "Compare with [BATCH] Chunk X/Y from /api/core/trigger-batch/"
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )
    except Exception as e:
        logger.error("[BATCH-NAIVE] Failed to trigger naive batch task: %s", e)
        return Response(
            {"error": f"Failed to queue naive batch task: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
