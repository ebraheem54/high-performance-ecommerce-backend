"""
Async Celery tasks for products app.
"""

from celery import shared_task


@shared_task
def release_expired_locks_task():
    """
    Periodic task: release all expired order locks.
    Scheduled via Celery Beat (e.g. every 5 minutes).
    """
    from apps.products.services import release_expired_locks
    count = release_expired_locks()
    return f"Released {count} expired order locks."


@shared_task
def invalidate_product_cache():
    """Invalidate the Redis product list cache after bulk changes."""
    from django.core.cache import cache
    cache.delete("product_list")
    return "Product list cache invalidated."


@shared_task
def alert_low_stock(product_id: int, threshold: int = 10):
    """
    Check if a product stock is below the threshold and send a notification.
    Called after every stock deduction.
    """
    from apps.products.models import Product
    from notifications.services import create_notification_for_admins

    try:
        product = Product.objects.get(id=product_id)
        if product.stock <= threshold:
            create_notification_for_admins(
                title=f"Low stock alert: {product.name}",
                message=f"Stock for '{product.name}' is down to {product.stock} units.",
                notification_type="STOCK_ALERT",
            )
    except Product.DoesNotExist:
        pass
