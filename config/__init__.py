"""
Import Celery app so it is initialized when Django starts.
The `celery` name must be exposed here so `celery -A config` can find it."""

from config.celery import app as celery_app

# expose as both names — celery CLI looks for 'celery' attribute on the module
celery = celery_app

# expose as both names — celery CLI looks for 'celery' attribute on the module
celery = celery_app
__all__ = ("celery_app", "celery")
