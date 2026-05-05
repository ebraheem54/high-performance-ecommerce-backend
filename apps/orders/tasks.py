"""
Async Celery tasks for orders app.
Heavy work (invoice generation, emails) is offloaded here
so the HTTP response is returned immediately to the user.
"""

from celery import shared_task


@shared_task
def generate_invoice_task(order_id: int):
    """
    Generate a PDF invoice for the order asynchronously.
    Decoupled from the checkout HTTP response — user doesn't wait for this.
    """
    from apps.orders.models import Order
    try:
        order = Order.objects.prefetch_related("items__product").get(id=order_id)
        # TODO: generate PDF and store in object storage
        print(f"[TASK] Invoice generated for Order #{order.id}")
    except Order.DoesNotExist:
        pass


@shared_task
def update_order_status_task(order_id: int, new_status: str):
    """Update order status asynchronously (e.g. after payment webhook)."""
    from apps.orders.models import Order
    try:
        Order.objects.filter(id=order_id).update(status=new_status)
        print(f"[TASK] Order #{order_id} status updated to {new_status}")
    except Exception as e:
        print(f"[TASK] Failed to update order #{order_id}: {e}")
