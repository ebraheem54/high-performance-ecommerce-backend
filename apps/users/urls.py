"""
URL mappings for the users API.
"""

from django.urls import path
from apps.users import views

app_name = "users"

urlpatterns = [
    # Open to everyone
    path("register/",   views.RegisterView.as_view(),    name="register"),
    path("login/",      views.LoginView.as_view(),       name="login"),

    # Authenticated
    path("logout/",     views.LogoutView.as_view(),      name="logout"),
    path("me/",         views.ProfileView.as_view(),     name="me"),

    # Admin only
    path("",            views.UserListView.as_view(),    name="user-list"),
    path("<int:pk>/",   views.UserDetailView.as_view(),  name="user-detail"),
]
