"""
Async Celery tasks for cart app.
"""

from celery import shared_task


@shared_task
def cleanup_abandoned_carts(days_old: int = 30):
    """
    Remove cart items that haven't been updated in X days.
    Runs as a scheduled Celery Beat job.
    """
    from django.utils import timezone
    from datetime import timedelta
    from apps.cart.models import CartItem

    cutoff = timezone.now() - timedelta(days=days_old)
    deleted, _ = CartItem.objects.filter(updated_at__lt=cutoff).delete()
    return f"Deleted {deleted} abandoned cart items older than {days_old} days."
