"""
Serializers for the user API.
"""

from django.contrib.auth import get_user_model, authenticate
from django.utils.translation import gettext as _
from rest_framework import serializers


class UserSerializer(serializers.ModelSerializer):
    """Serializer for user object."""

    class Meta:
        model = get_user_model()
        fields = ["id", "email", "password", "name", "created_at"]
        extra_kwargs = {
            "password": {"write_only": True, "min_length": 8},
            "created_at": {"read_only": True},
            "id": {"read_only": True},
        }

    def create(self, validated_data):
        """Create and return user with encrypted password."""
        return get_user_model().objects.create_user(**validated_data)

    def update(self, instance, validated_data):
        """Update and return user."""
        password = validated_data.pop("password", None)
        user = super().update(instance, validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user


class AdminUserSerializer(serializers.ModelSerializer):
    """Serializer for admin — shows all user fields including is_staff/is_active."""

    class Meta:
        model = get_user_model()
        fields = ["id", "email", "name", "is_staff", "is_active", "created_at"]
        extra_kwargs = {
            "id":         {"read_only": True},
            "email":      {"read_only": True},
            "created_at": {"read_only": True},
        }


class AuthTokenSerializer(serializers.Serializer):
    """Serializer for the user auth token."""

    email = serializers.EmailField()
    password = serializers.CharField(
        style={"input_type": "password"},
        trim_whitespace=False,
    )

    def validate(self, attrs):
        """Validate and authenticate the user."""
        email = attrs.get("email")
        password = attrs.get("password")
        user = authenticate(
            request=self.context.get("request"),
            username=email,
            password=password,
        )
        if not user:
            msg = _("Unable to authenticate with provided credentials.")
            raise serializers.ValidationError(msg, code="authorization")
        attrs["user"] = user
        return attrs
