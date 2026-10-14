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
