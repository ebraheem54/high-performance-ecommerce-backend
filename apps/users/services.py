"""
Business logic for users.
Handles user-related operations separate from the view layer.
"""

from django.contrib.auth import get_user_model

User = get_user_model()


def get_user_by_email(email: str):
    """Fetch a user by email. Returns None if not found."""
    try:
        return User.objects.get(email=email)
    except User.DoesNotExist:
        return None


def deactivate_user(user_id: int) -> bool:
    """Deactivate a user account by ID."""
    try:
        user = User.objects.get(id=user_id)
        user.is_active = False
        user.save(update_fields=["is_active"])
        return True
    except User.DoesNotExist:
        return FalseThrottle
