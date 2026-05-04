"""
API endpoints for user management.

Permissions:
  - Anyone      : register, login
  - Authenticated: logout, view/update own profile
  - Admin only  : list all users, deactivate a user
"""

from rest_framework import generics, authentication, status
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from rest_framework.settings import api_settings
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth import get_user_model

from apps.users.serializers import UserSerializer, AuthTokenSerializer, AdminUserSerializer

User = get_user_model()


class RegisterView(generics.CreateAPIView):
    """
    POST /api/users/register/
    Open to everyone — no auth required.
    """
    serializer_class     = UserSerializer
    permission_classes   = []
    authentication_classes = []


class LoginView(ObtainAuthToken):
    """
    POST /api/users/login/
    Returns an auth token.
    """
    serializer_class = AuthTokenSerializer
    renderer_classes = api_settings.DEFAULT_RENDERER_CLASSES


class LogoutView(APIView):
    """
    POST /api/users/logout/
    Deletes the auth token (server-side logout).
    """
    authentication_classes = [authentication.TokenAuthentication]
    permission_classes     = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response({"message": "Logged out successfully."})


class ProfileView(generics.RetrieveUpdateAPIView):
    """
    GET   /api/users/me/   — any authenticated user (own profile)
    PATCH /api/users/me/   — any authenticated user (own profile)
    """
    serializer_class       = UserSerializer
    authentication_classes = [authentication.TokenAuthentication]
    permission_classes     = [IsAuthenticated]

    def get_object(self):
        return self.request.user


# ─────────────────────────────────────────────
# Admin-only endpoints
# ─────────────────────────────────────────────

class UserListView(generics.ListAPIView):
    """
    GET /api/users/        — admin only: list all users
    """
    serializer_class   = AdminUserSerializer
    permission_classes = [IsAdminUser]
    queryset           = User.objects.all().order_by("-created_at")

class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/users/<id>/   — admin: view any user
    PATCH  /api/users/<id>/   — admin: update any user (e.g. is_active)
    DELETE /api/users/<id>/   — admin: deactivate (soft delete)
    """
    serializer_class   = AdminUserSerializer
    permission_classes = [IsAdminUser]
    queryset         = User.objects.all()

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        user.is_active = False
        user.save(update_fields=["is_active"])
        return Response({"message": f"User '{user.email}' deactivated."})
