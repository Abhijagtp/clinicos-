from django.urls import path
from .views import LoginView, RefreshTokenView, UserProfileView, LogoutView

urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("refresh/", RefreshTokenView.as_view(), name="auth-refresh"),
    path("me/", UserProfileView.as_view(), name="auth-me"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
]
