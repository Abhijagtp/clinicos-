from django.urls import path
from .views import (
    LoginView,
    RefreshTokenView,
    UserProfileView,
    LogoutView,
    StaffListCreateView,
    StaffDetailView,
)

urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("refresh/", RefreshTokenView.as_view(), name="auth-refresh"),
    path("me/", UserProfileView.as_view(), name="auth-me"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),

    # Staff & Team Management
    path("staff/", StaffListCreateView.as_view(), name="staff-list-create"),
    path("staff/<int:id>/", StaffDetailView.as_view(), name="staff-detail"),
]
