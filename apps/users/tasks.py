"""
Async Celery tasks for users app.
E.g. sending welcome emails, account verification emails.
"""

from celery import shared_task


@shared_task
def send_welcome_email(user_id: int):
    """Send a welcome email to a newly registered user."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
        # TODO: integrate email backend (e.g. Django send_mail)
        print(f"[TASK] Welcome email sent to {user.email}")
    except User.DoesNotExist:
        pass


@shared_task
def send_password_reset_email(user_id: int, reset_token: str):
    """Send a password reset email."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
        print(f"[TASK] Password reset email sent to {user.email}")
    except User.DoesNotExist:
        pass
