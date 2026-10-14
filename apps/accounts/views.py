import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .serializers import (
    CustomTokenObtainPairSerializer,
    UserProfileSerializer,
    LogoutSerializer,
)

logger = logging.getLogger(__name__)


class LoginView(TokenObtainPairView):
    """
    POST /api/v1/auth/login/
    Authenticates clinic staff with email and password, returning JWT tokens and user profile.
    """
    permission_classes = [AllowAny]
    serializer_class = CustomTokenObtainPairSerializer


class RefreshTokenView(TokenRefreshView):
    """
    POST /api/v1/auth/refresh/
    Refreshes an expired access token using a valid refresh token.
    """
    permission_classes = [AllowAny]


class UserProfileView(APIView):
    """
    GET /api/v1/auth/me/
    Retrieves the profile, role, and clinic details of the currently authenticated staff user.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """
    POST /api/v1/auth/logout/
    Blacklists the staff member's refresh token on sign out.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"message": "Successfully logged out."}, status=status.HTTP_200_OK)
