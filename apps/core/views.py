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
# ⚠ DEMO ONLY — Capacity Stress View (Requirement 2 — BEFORE solution context)
# ══════════════════════════════════════════════════════════════════════════════
# Purpose:
#   Simulate a heavy endpoint that runs several sequential DB queries with
#   small sleep delays. Used to create measurable load for Locust screenshots
#   and pg_stat_activity comparisons.
#
# IMPORTANT: This route alone does NOT prove CONN_MAX_AGE.
# It is a CAPACITY STRESS DEMO only.
#
# Real before/after evidence requires:
#   Before: CONN_MAX_AGE=0  → restart server → run Locust → capture pg_stat_activity
#   After:  CONN_MAX_AGE=60 → restart server → run Locust → capture pg_stat_activity
#
#   Commands:
#     # Count open DB connections during load:
#     psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database();"
#
#     # Before (new connection per request):
#     CONN_MAX_AGE=0 python manage.py runserver 0.0.0.0:5000
#
#     # After (connections reused for 60s):
#     CONN_MAX_AGE=60 python manage.py runserver 0.0.0.0:5000
#
# To REMOVE: delete this function + its URL pattern in urls.py.
# ══════════════════════════════════════════════════════════════════════════════

@api_view(["POST"])
@permission_classes([IsAdminUser])
def capacity_stress_view(request):
    """
    POST /api/core/capacity-stress/  — admin only

    ⚠ DEMO ONLY — Capacity stress endpoint for Requirement 2.
    Runs 5 sequential DB queries with sleep delays to create measurable load.
    Returns elapsed time and current DB connection count from pg_stat_activity.
    """
    import time as _time
    from django.db import connection

    started = _time.time()
    query_times = []

    # ── 5 sequential DB queries with sleep between them ───────────────────────
    # Simulates a poorly optimised endpoint that holds a DB connection
    # open for an extended period under concurrent load.
    from apps.products.models import Product
    from apps.orders.models import Order

    for i in range(1, 6):
        q_start = _time.time()
        _ = list(Product.objects.filter(is_active=True).order_by("?")[:20])
        _time.sleep(0.05)   # 50ms simulated work between queries
        query_times.append(round(_time.time() - q_start, 3))

    # ── Read current DB connection count from pg_stat_activity ────────────────
    db_conn_count = None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database();"
            )
            db_conn_count = cursor.fetchone()[0]
    except Exception as exc:
        logger.warning("[CAPACITY-STRESS] Could not read pg_stat_activity: %s", exc)

    elapsed = round(_time.time() - started, 3)

    logger.info(
        "[CAPACITY-STRESS] ⚠ DEMO — stress request completed in %.3fs | "
        "open_db_connections=%s",
        elapsed, db_conn_count,
    )

    return Response(
        {
            "_demo"              : "⚠ DEMO ONLY — capacity stress endpoint (Req 2)",
            "elapsed_s"          : elapsed,
            "query_count"        : 5,
            "query_times_s"      : query_times,
            "open_db_connections": db_conn_count,
            "instructions"       : {
                "before": "Set CONN_MAX_AGE=0, restart server, run Locust, read pg_stat_activity",
                "after" : "Set CONN_MAX_AGE=60, restart server, run Locust, read pg_stat_activity",
                "query" : "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database();",
            },
        }
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
def trigger_batch_naive_view(request):
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

        days_back = int(request.data.get("days_back", 1))
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
