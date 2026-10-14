import pytest
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from apps.clinics.models import Clinic, ClinicMembership

User = get_user_model()


@pytest.fixture
def clinic(db):
    return Clinic.objects.create(
        name="Apex Dental Care",
        timezone="Asia/Kolkata",
        status=Clinic.Status.ACTIVE,
    )


@pytest.fixture
def staff_user(db, clinic):
    user = User.objects.create_user(
        email="doctor.sharma@apexdental.com",
        password="SecurePassword123!",
        first_name="Dr. Priya",
        last_name="Sharma",
    )
    ClinicMembership.objects.create(
        clinic=clinic,
        user=user,
        role=ClinicMembership.Role.DOCTOR,
        is_active=True,
    )
    return user


@pytest.fixture
def receptionist_user(db, clinic):
    user = User.objects.create_user(
        email="reception@apexdental.com",
        password="ReceptionPassword123!",
        first_name="Ananya",
        last_name="Verma",
    )
    ClinicMembership.objects.create(
        clinic=clinic,
        user=user,
        role=ClinicMembership.Role.RECEPTIONIST,
        is_active=True,
    )
    return user


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
class TestAuthAPI:
    def test_login_success(self, api_client, staff_user, clinic):
        response = api_client.post(
            "/api/v1/auth/login/",
            {"email": "doctor.sharma@apexdental.com", "password": "SecurePassword123!"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert "access" in data
        assert "refresh" in data
        assert "user" in data

        user_info = data["user"]
        assert user_info["email"] == "doctor.sharma@apexdental.com"
        assert user_info["name"] == "Dr. Priya Sharma"
        assert user_info["role"] == "doctor"
        assert user_info["clinic"]["name"] == "Apex Dental Care"

    def test_login_invalid_password(self, api_client, staff_user):
        response = api_client.post(
            "/api/v1/auth/login/",
            {"email": "doctor.sharma@apexdental.com", "password": "WrongPassword!"},
            format="json",
        )
        assert response.status_code == 401

    def test_token_refresh(self, api_client, staff_user):
        login_res = api_client.post(
            "/api/v1/auth/login/",
            {"email": "doctor.sharma@apexdental.com", "password": "SecurePassword123!"},
            format="json",
        )
        refresh_token = login_res.json()["refresh"]

        refresh_res = api_client.post(
            "/api/v1/auth/refresh/",
            {"refresh": refresh_token},
            format="json",
        )
        assert refresh_res.status_code == 200
        assert "access" in refresh_res.json()

    def test_user_profile_me_authenticated(self, api_client, receptionist_user):
        login_res = api_client.post(
            "/api/v1/auth/login/",
            {"email": "reception@apexdental.com", "password": "ReceptionPassword123!"},
            format="json",
        )
        access_token = login_res.json()["access"]

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        me_res = api_client.get("/api/v1/auth/me/")

        assert me_res.status_code == 200
        data = me_res.json()
        assert data["email"] == "reception@apexdental.com"
        assert data["name"] == "Ananya Verma"
        assert data["role"] == "receptionist"
        assert data["clinic"]["name"] == "Apex Dental Care"

    def test_user_profile_unauthenticated(self, api_client):
        response = api_client.get("/api/v1/auth/me/")
        assert response.status_code == 401

    def test_logout_blacklists_token(self, api_client, staff_user):
        login_res = api_client.post(
            "/api/v1/auth/login/",
            {"email": "doctor.sharma@apexdental.com", "password": "SecurePassword123!"},
            format="json",
        )
        access_token = login_res.json()["access"]
        refresh_token = login_res.json()["refresh"]

        # Logout with refresh token
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        logout_res = api_client.post(
            "/api/v1/auth/logout/",
            {"refresh": refresh_token},
            format="json",
        )
        assert logout_res.status_code == 200
        assert "logged out" in logout_res.json()["message"].lower()

        # Attempt to use blacklisted refresh token
        reuse_res = api_client.post(
            "/api/v1/auth/refresh/",
            {"refresh": refresh_token},
            format="json",
        )
        assert reuse_res.status_code == 401
