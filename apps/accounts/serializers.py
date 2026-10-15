from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from django.contrib.auth import authenticate
from apps.accounts.models import User
from apps.clinics.models import ClinicMembership, Clinic


class UserProfileSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    clinic = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "name",
            "role",
            "clinic",
            "is_staff",
            "is_superuser",
            "last_login",
            "date_joined",
        ]

    def get_name(self, obj) -> str:
        full = f"{obj.first_name} {obj.last_name}".strip()
        return full or obj.email.split("@")[0].capitalize()

    def get_role(self, obj) -> str:
        if obj.is_superuser:
            return "owner"
        membership = ClinicMembership.objects.filter(user=obj, is_active=True).first()
        return membership.role if membership else "staff"

    def get_clinic(self, obj):
        membership = ClinicMembership.objects.filter(user=obj, is_active=True).select_related("clinic").first()
        if membership and membership.clinic:
            return {
                "id": str(membership.clinic.id),
                "name": membership.clinic.name,
                "timezone": membership.clinic.timezone,
            }
        # Fallback to active clinic if single-client deployment
        clinic = Clinic.objects.filter(status=Clinic.Status.ACTIVE).first()
        if clinic:
            return {
                "id": str(clinic.id),
                "name": clinic.name,
                "timezone": clinic.timezone,
            }
        return None


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Custom JWT login serializer returning access, refresh, and staff user profile with clinic role.
    """

    def validate(self, attrs):
        data = super().validate(attrs)

        user = self.user
        profile = UserProfileSerializer(user).data
        data["user"] = profile

        return data


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=True, help_text="The refresh token to blacklist")

    def validate(self, attrs):
        self.token = attrs["refresh"]
        return attrs

    def save(self, **kwargs):
        try:
            RefreshToken(self.token).blacklist()
        except TokenError as e:
            raise serializers.ValidationError({"refresh": str(e)})


class StaffMemberSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    clinic_id = serializers.SerializerMethodField()
    clinic_name = serializers.SerializerMethodField()
    membership_id = serializers.SerializerMethodField()
    membership_active = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "membership_id",
            "email",
            "first_name",
            "last_name",
            "name",
            "role",
            "clinic_id",
            "clinic_name",
            "is_active",
            "membership_active",
            "is_staff",
            "is_superuser",
            "last_login",
            "date_joined",
        ]
        read_only_fields = [
            "id",
            "membership_id",
            "name",
            "role",
            "clinic_id",
            "clinic_name",
            "membership_active",
            "is_staff",
            "is_superuser",
            "last_login",
            "date_joined",
        ]

    def _get_membership(self, obj):
        clinic = self.context.get("clinic")
        if clinic:
            return ClinicMembership.objects.filter(user=obj, clinic=clinic).first()
        return ClinicMembership.objects.filter(user=obj).select_related("clinic").first()

    def get_name(self, obj) -> str:
        full = f"{obj.first_name} {obj.last_name}".strip()
        return full or obj.email.split("@")[0].capitalize()

    def get_role(self, obj) -> str:
        if obj.is_superuser:
            return "owner"
        membership = self._get_membership(obj)
        return membership.role if membership else "staff"

    def get_membership_id(self, obj):
        membership = self._get_membership(obj)
        return str(membership.id) if membership else None

    def get_membership_active(self, obj) -> bool:
        membership = self._get_membership(obj)
        return membership.is_active if membership else obj.is_active

    def get_clinic_id(self, obj):
        membership = self._get_membership(obj)
        return str(membership.clinic_id) if membership else None

    def get_clinic_name(self, obj):
        membership = self._get_membership(obj)
        return membership.clinic.name if membership and membership.clinic else None


class StaffCreateSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, required=True, min_length=6)
    first_name = serializers.CharField(required=True, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, default="", max_length=150)
    role = serializers.ChoiceField(
        choices=ClinicMembership.Role.choices,
        default=ClinicMembership.Role.RECEPTIONIST,
    )
    clinic_id = serializers.UUIDField(required=False)

    def validate_email(self, value):
        normalized = value.strip().lower()
        if User.objects.filter(email=normalized).exists():
            raise serializers.ValidationError("A user with this email address already exists.")
        return normalized


class StaffUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(required=False, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    role = serializers.ChoiceField(choices=ClinicMembership.Role.choices, required=False)
    is_active = serializers.BooleanField(required=False)
    password = serializers.CharField(write_only=True, required=False, min_length=6)

