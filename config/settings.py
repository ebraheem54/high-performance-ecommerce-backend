"""
Django settings for High-Performance E-Commerce Backend.
Based on Django 4.2
"""

import os
import sys

from pathlib import Path
from decouple import config
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
# Add apps/ to Python path so apps can be imported as bare modules
# e.g. `from products import views` instead of `from apps.products import views`


# ── Security ──────────────────────────────────────────────────────────────────
SECRET_KEY = config("SECRET_KEY", default="django-insecure-change-me-in-production")
DEBUG = config("DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="*").split(",")

# ── Custom User Model ─────────────────────────────────────────────────────────
AUTH_USER_MODEL = "users.User"
# ── Installed Apps ────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    # Django built-ins
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "django_celery_beat",
    "drf_spectacular",
    # Local apps
    "apps.users",
    "apps.products",
    "apps.orders",
    "apps.cart",
    "apps.core",
    # "apps.notifications",
    # "apps.reports",
]

# ── Middleware ────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",  # must be before CommonMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ── Database (PostgreSQL) ─────────────────────────────────────────────────────
_DATABASE_URL = os.environ.get("DATABASE_URL")
if _DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(_DATABASE_URL)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": config("DB_NAME", default="ecommerce_db"),
            "USER": config("DB_USER", default="postgres"),
            "PASSWORD": config("DB_PASSWORD", default="postgres"),
            "HOST": config("DB_HOST", default="localhost"),
            "PORT": config("DB_PORT", default="5432"),
        }
    }


# ── DB Connection Pooling — Resource Management (Requirement 2) ───────────────
DATABASES["default"]["CONN_MAX_AGE"] = config("CONN_MAX_AGE", default=60, cast=int)


# ── Password Validation ───────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── Internationalization ──────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Django REST Framework ─────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # AnonRateThrottle + UserRateThrottle protect the server from abuse
    # and prevent a single client from starving shared DB connections.
    "DEFAULT_THROTTLE_CLASSES": [

    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "1000/min",
        "user": "10000/min",
    },
}


# ── Redis Cache — Distributed Caching (Requirement 6) ────────────────────────
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": config("REDIS_URL", default="redis://localhost:6379/0"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

# ── Celery — Async Queues (Requirement 3) ─────────────────────────────────────
CELERY_BROKER_URL = config("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = config("REDIS_URL", default="redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# Worker concurrency — Resource Management (Requirement 2)
# Controls max parallel tasks: celery -A config worker --concurrency=4
CELERY_WORKER_CONCURRENCY = config("CELERY_CONCURRENCY", default=4, cast=int)

# ── Celery Beat Schedule — Batch Processing (Requirement 4) ──────────────────
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    # Nightly batch: aggregate daily sales at 1:00 AM — Requirement 4
    "daily-sales-report": {
        "task": "apps.core.tasks.run_daily_sales_batch_task",
        "schedule": crontab(hour=1, minute=0),
    },
    # Release expired order locks every 5 minutes
    "release-expired-locks": {
        "task": "apps.products.tasks.release_expired_locks_task",
        "schedule": crontab(minute="*/5"),
    },
    # Clean up abandoned carts every Sunday at midnight — Requirement 4
    "cleanup-abandoned-carts": {
        "task": "apps.cart.tasks.cleanup_abandoned_carts",
        "schedule": crontab(hour=0, minute=0, day_of_week=0),
    },
}

# ── Celery Queue Routing — Async Queues (Requirement 3) ──────────────────────
# Separates tasks into dedicated queues so a flood of email tasks cannot block
# the batch processing queue.
#
# Run workers (after-solution setup):
#   Email worker:  celery -A config worker -Q emails   --loglevel=info -c 4
#   Batch worker:  celery -A config worker -Q batch    --loglevel=info -c 2
#   Default worker:celery -A config worker -Q celery   --loglevel=info -c 4
#
# Single worker (all queues together — before-solution comparison):
#   celery -A config worker -Q emails,batch,celery --loglevel=info
#
# To REMOVE: delete this entire block (CELERY_TASK_QUEUES + CELERY_TASK_ROUTES).
from kombu import Queue as _Queue

CELERY_TASK_QUEUES = (
    _Queue("emails"),    # Email notification tasks (Req 3)
    _Queue("batch"),     # Batch processing tasks   (Req 4)
    _Queue("celery"),    # General / default tasks
)

CELERY_TASK_ROUTES = {
    # ── Email tasks → emails queue ─────────────────────────────────────────────
    "apps.orders.tasks.send_order_confirmation_email": {"queue": "emails"},
    "apps.orders.tasks.send_order_cancelled_email":    {"queue": "emails"},
    # ── Batch tasks → batch queue ──────────────────────────────────────────────
    "apps.core.tasks.run_daily_sales_batch_task":      {"queue": "batch"},
    "apps.cart.tasks.cleanup_abandoned_carts":          {"queue": "batch"},
    # ── General tasks → default celery queue ──────────────────────────────────
    "apps.products.tasks.release_expired_locks_task":  {"queue": "celery"},
    "apps.products.tasks.invalidate_product_cache":    {"queue": "celery"},
}

# ── CORS ──────────────────────────────────────────────────────────────────────
CORS_ALLOW_ALL_ORIGINS = DEBUG

# ── API Docs (drf-spectacular) ────────────────────────────────────────────────
SPECTACULAR_SETTINGS = {
    "TITLE": "High-Performance E-Commerce API",
    "DESCRIPTION": "E-Commerce Backend with concurrency control, Redis caching, and batch processing.",
    "VERSION": "1.0.0",
}



_email_host = os.getenv("EMAIL_HOST")
_email_port = os.getenv("EMAIL_PORT")

if _email_host and _email_port:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = _email_host
    EMAIL_PORT = int(_email_port)
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = True
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="noreply@ecommerce.dev")
