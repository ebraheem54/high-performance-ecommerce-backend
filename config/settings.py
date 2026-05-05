"""
Django settings for High-Performance E-Commerce Backend.
Based on Django 4.2
"""
import os
import sys

from pathlib import Path
from decouple import config

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
    'apps.core',
    # "apps.notifications",
    # "apps.reports",

]

# ── Middleware ────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",          # must be before CommonMiddleware
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
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME":     config("DB_NAME",     default="ecommerce_db"),
        "USER":     config("DB_USER",     default="postgres"),
        "PASSWORD": config("DB_PASSWORD", default="postgres"),
        "HOST":     config("DB_HOST",     default="db"),
        "PORT":     config("DB_PORT",     default="5432"),

    }
}

# ── Password Validation ───────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
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
    # Throttling — Resource Management (Requirement 2)
    "DEFAULT_THROTTLE_CLASSES": [],
  "DEFAULT_THROTTLE_RATES": {
    "anon": "200/min",
    "user": "2000/min",
}
}

# ── Redis Cache — Distributed Caching (Requirement 6) ────────────────────────
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": config("REDIS_URL", default="redis://redis:6379/0"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

# ── Celery — Async Queues (Requirement 3) ─────────────────────────────────────
CELERY_BROKER_URL      = config("REDIS_URL", default="redis://redis:6379/0")
CELERY_RESULT_BACKEND  = config("REDIS_URL", default="redis://redis:6379/0")
CELERY_ACCEPT_CONTENT  = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# Worker concurrency — Resource Management (Requirement 2)
# Controls max parallel tasks: celery -A config worker --concurrency=4
CELERY_WORKER_CONCURRENCY = config("CELERY_CONCURRENCY", default=4, cast=int)

# ── Celery Beat Schedule — Batch Processing (Requirement 4) ──────────────────
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    # Nightly batch: aggregate daily sales at 1:00 AM
    "daily-sales-report": {
        "task": "reports.tasks.run_daily_sales_batch_task",
        "schedule": crontab(hour=1, minute=0),
    },
    # Release expired order locks every 5 minutes
    "release-expired-locks": {
        "task": "products.tasks.release_expired_locks_task",
        "schedule": crontab(minute="*/5"),
    },
    # Clean up abandoned carts every Sunday at midnight
    "cleanup-abandoned-carts": {
        "task": "cart.tasks.cleanup_abandoned_carts",
        "schedule": crontab(hour=0, minute=0, day_of_week=0),
    },
}

# ── CORS ──────────────────────────────────────────────────────────────────────
CORS_ALLOW_ALL_ORIGINS = DEBUG

# ── API Docs (drf-spectacular) ────────────────────────────────────────────────
SPECTACULAR_SETTINGS = {
    "TITLE": "High-Performance E-Commerce API",
    "DESCRIPTION": "E-Commerce Backend with concurrency control, Redis caching, and batch processing.",
    "VERSION": "1.0.0",
}
