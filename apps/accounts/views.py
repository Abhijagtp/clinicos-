import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from django.shortcuts import get_object_or_404
from django.db import transaction
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError
from apps.accounts.models import User
from apps.clinics.models import Clinic, ClinicMembership

from .serializers import (
    CustomTokenObtainPairSerializer,
    UserProfileSerializer,
    LogoutSerializer,
    StaffMemberSerializer,
    StaffCreateSerializer,
    StaffUpdateSerializer,
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


def _get_clinic_and_membership(user):
    membership = (
        ClinicMembership.objects.filter(user=user, is_active=True)
        .select_related("clinic")
        .first()
    )
    if membership:
        return membership.clinic, membership
    if user.is_superuser:
        clinic = Clinic.objects.filter(status=Clinic.Status.ACTIVE).first()
        return clinic, None
    return None, None


def _can_manage_staff(user, membership):
    return bool(
        user.is_superuser
        or (membership and membership.role in [ClinicMembership.Role.OWNER, ClinicMembership.Role.DOCTOR])
    )


class StaffListCreateView(APIView):
    """
    GET  /api/v1/auth/staff/ — List all staff members in the clinic
    POST /api/v1/auth/staff/ — Create a new staff account and clinic membership
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        clinic, membership = _get_clinic_and_membership(request.user)
        if not clinic:
            return Response({"error": "No active clinic found."}, status=status.HTTP_404_NOT_FOUND)

        # Retrieve all staff with memberships in this clinic
        memberships = (
            ClinicMembership.objects.filter(clinic=clinic)
            .select_related("user")
            .order_by("-is_active", "created_at")
        )

        role_filter = request.query_params.get("role")
        if role_filter:
            memberships = memberships.filter(role=role_filter)

        users = [m.user for m in memberships]
        serializer = StaffMemberSerializer(users, many=True, context={"clinic": clinic})
        return Response({
            "clinic_id": str(clinic.id),
            "clinic_name": clinic.name,
            "can_manage_staff": _can_manage_staff(request.user, membership),
            "count": len(users),
            "results": serializer.data,
        }, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        clinic, membership = _get_clinic_and_membership(request.user)
        if not clinic:
            return Response({"error": "No active clinic found."}, status=status.HTTP_404_NOT_FOUND)

        # RBAC Check: Only Owners or Doctors (or superusers) can add staff
        if not _can_manage_staff(request.user, membership):
            raise PermissionDenied("Only clinic owners or doctors have permission to create staff accounts.")

        serializer = StaffCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        target_clinic = clinic
        if request.user.is_superuser and data.get("clinic_id"):
            from apps.clinics.models import Clinic as ClinicModel
            target_clinic = get_object_or_404(ClinicModel, id=data["clinic_id"])

        from django.db import transaction
        with transaction.atomic():
            user = User.objects.create_user(
                email=data["email"],
                password=data["password"],
                first_name=data["first_name"],
                last_name=data.get("last_name", ""),
            )
            ClinicMembership.objects.create(
                clinic=target_clinic,
                user=user,
                role=data["role"],
                is_active=True,
            )

        logger.info(
            "Created staff user=%s role=%s in clinic=%s by user=%s",
            user.id,
            data["role"],
            target_clinic.id,
            request.user.id,
        )

        response_data = StaffMemberSerializer(user, context={"clinic": target_clinic}).data
        return Response(response_data, status=status.HTTP_201_CREATED)


class StaffDetailView(APIView):
    """
    GET    /api/v1/auth/staff/<int:id>/ — Get staff profile
    PATCH  /api/v1/auth/staff/<int:id>/ — Update role, name, or password
    DELETE /api/v1/auth/staff/<int:id>/ — Deactivate staff member
    """
    permission_classes = [IsAuthenticated]

    def _get_target_user_and_membership(self, requester, target_id):
        clinic, requester_membership = _get_clinic_and_membership(requester)
        if not clinic:
            return None, None, None, None

        from apps.accounts.models import User as UserModel
        target_user = UserModel.objects.filter(id=target_id).first()
        if not target_user:
            return None, None, None, None

        target_membership = ClinicMembership.objects.filter(
            clinic=clinic,
            user=target_user,
        ).first()

        if not target_membership and not requester.is_superuser:
            return None, None, None, None

        return clinic, requester_membership, target_user, target_membership

    def get(self, request, id, *args, **kwargs):
        clinic, _, target_user, target_membership = self._get_target_user_and_membership(request.user, id)
        if not target_user:
            return Response({"error": "Staff member not found in your clinic."}, status=status.HTTP_404_NOT_FOUND)

        serializer = StaffMemberSerializer(target_user, context={"clinic": clinic})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, id, *args, **kwargs):
        clinic, requester_membership, target_user, target_membership = self._get_target_user_and_membership(
            request.user, id
        )
        if not target_user:
            return Response({"error": "Staff member not found in your clinic."}, status=status.HTTP_404_NOT_FOUND)

        if not _can_manage_staff(request.user, requester_membership):
            raise PermissionDenied("Only clinic owners or doctors have permission to update staff members.")

        from .serializers import StaffUpdateSerializer
        serializer = StaffUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Update user fields
        update_user_fields = []
        if "first_name" in data:
            target_user.first_name = data["first_name"]
            update_user_fields.append("first_name")
        if "last_name" in data:
            target_user.last_name = data["last_name"]
            update_user_fields.append("last_name")
        if "password" in data:
            target_user.set_password(data["password"])
            update_user_fields.append("password")
        if "is_active" in data:
            if target_user == request.user and not data["is_active"]:
                return Response(
                    {"error": "You cannot deactivate your own account."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            target_user.is_active = data["is_active"]
            update_user_fields.append("is_active")

        if update_user_fields:
            target_user.save(update_fields=update_user_fields)

        # Update membership fields
        if target_membership:
            update_membership_fields = []
            if "role" in data:
                target_membership.role = data["role"]
                update_membership_fields.append("role")
            if "is_active" in data:
                target_membership.is_active = data["is_active"]
                update_membership_fields.append("is_active")

            if update_membership_fields:
                target_membership.save(update_fields=update_membership_fields)

        logger.info("Updated staff user=%s by requester=%s", target_user.id, request.user.id)
        return Response(
            StaffMemberSerializer(target_user, context={"clinic": clinic}).data,
            status=status.HTTP_200_OK,
        )

    def delete(self, request, id, *args, **kwargs):
        clinic, requester_membership, target_user, target_membership = self._get_target_user_and_membership(
            request.user, id
        )
        if not target_user:
            return Response({"error": "Staff member not found in your clinic."}, status=status.HTTP_404_NOT_FOUND)

        if not _can_manage_staff(request.user, requester_membership):
            raise PermissionDenied("Only clinic owners or doctors have permission to deactivate staff members.")

        if target_user == request.user:
            return Response(
                {"error": "You cannot deactivate your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if target_membership:
            target_membership.is_active = False
            target_membership.save(update_fields=["is_active"])
        target_user.is_active = False
        target_user.save(update_fields=["is_active"])

        logger.info("Deactivated staff user=%s by requester=%s", target_user.id, request.user.id)
        return Response(
            {"detail": "Staff member deactivated successfully.", "is_active": False},
            status=status.HTTP_200_OK,
        )

