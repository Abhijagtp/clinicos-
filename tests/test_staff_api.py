import pytest
from django.urls import reverse
from apps.accounts.models import User
from apps.clinics.models import Clinic, ClinicMembership


@pytest.fixture
def receptionist_user(db, clinic):
    user = User.objects.create_user(
        email="receptionist@dentalcare.com",
        password="secretpassword123",
        first_name="Rani",
        last_name="Mukherjee",
    )
    ClinicMembership.objects.create(
        clinic=clinic,
        user=user,
        role=ClinicMembership.Role.RECEPTIONIST,
        is_active=True,
    )
    return user


@pytest.mark.django_db
class TestStaffAPI:

    def test_staff_unauthenticated_fails(self, api_client):
        url = reverse("staff-list-create")
        res = api_client.get(url)
        assert res.status_code == 401

        res_post = api_client.post(url, {"email": "new@clinic.com"})
        assert res_post.status_code == 401

    def test_list_staff_clinic_isolation(
        self, api_client, clinic, other_clinic, staff_user, other_clinic_user
    ):
        url = reverse("staff-list-create")
        api_client.force_authenticate(user=staff_user)

        res = api_client.get(url)
        assert res.status_code == 200
        data = res.json()
        assert data["clinic_id"] == str(clinic.id)
        assert data["count"] >= 1
        emails = [s["email"] for s in data["results"]]
        assert staff_user.email in emails
        assert other_clinic_user.email not in emails

    def test_create_staff_doctor_success(self, api_client, clinic, staff_user):
        url = reverse("staff-list-create")
        api_client.force_authenticate(user=staff_user)

        payload = {
            "email": "dr.kavita@dentalcare.com",
            "password": "strongPassword123!",
            "first_name": "Dr. Kavita",
            "last_name": "Rao",
            "role": "doctor",
        }

        res = api_client.post(url, payload, format="json")
        assert res.status_code == 201
        data = res.json()
        assert data["email"] == "dr.kavita@dentalcare.com"
        assert data["first_name"] == "Dr. Kavita"
        assert data["last_name"] == "Rao"
        assert data["role"] == "doctor"
        assert data["clinic_id"] == str(clinic.id)
        assert data["is_active"] is True

        # Check DB
        new_user = User.objects.get(email="dr.kavita@dentalcare.com")
        assert new_user.check_password("strongPassword123!")
        membership = ClinicMembership.objects.get(user=new_user, clinic=clinic)
        assert membership.role == ClinicMembership.Role.DOCTOR
        assert membership.is_active is True

    def test_create_staff_receptionist_forbidden(self, api_client, receptionist_user):
        url = reverse("staff-list-create")
        api_client.force_authenticate(user=receptionist_user)

        payload = {
            "email": "assistant@dentalcare.com",
            "password": "strongPassword123!",
            "first_name": "Anil",
            "role": "assistant",
        }

        res = api_client.post(url, payload, format="json")
        assert res.status_code == 403
        assert "permission" in res.json().get("detail", "").lower()

    def test_create_staff_duplicate_email_fails(self, api_client, staff_user):
        url = reverse("staff-list-create")
        api_client.force_authenticate(user=staff_user)

        payload = {
            "email": staff_user.email,  # duplicate
            "password": "strongPassword123!",
            "first_name": "Duplicate",
            "role": "receptionist",
        }

        res = api_client.post(url, payload, format="json")
        assert res.status_code == 400
        assert "email" in res.json()

    def test_update_staff_role_and_name(self, api_client, staff_user, receptionist_user):
        url = reverse("staff-detail", kwargs={"id": receptionist_user.id})
        api_client.force_authenticate(user=staff_user)

        res = api_client.patch(url, {
            "first_name": "Rani Kumari",
            "role": "assistant",
        }, format="json")

        assert res.status_code == 200
        data = res.json()
        assert data["first_name"] == "Rani Kumari"
        assert data["role"] == "assistant"

        receptionist_user.refresh_from_db()
        assert receptionist_user.first_name == "Rani Kumari"
        membership = ClinicMembership.objects.get(user=receptionist_user)
        assert membership.role == "assistant"

    def test_deactivate_staff_member(self, api_client, staff_user, receptionist_user):
        url = reverse("staff-detail", kwargs={"id": receptionist_user.id})
        api_client.force_authenticate(user=staff_user)

        res = api_client.delete(url)
        assert res.status_code == 200
        assert res.json()["is_active"] is False

        receptionist_user.refresh_from_db()
        assert receptionist_user.is_active is False
        membership = ClinicMembership.objects.get(user=receptionist_user)
        assert membership.is_active is False

    def test_cannot_deactivate_self(self, api_client, staff_user):
        url = reverse("staff-detail", kwargs={"id": staff_user.id})
        api_client.force_authenticate(user=staff_user)

        # Via DELETE
        res = api_client.delete(url)
        assert res.status_code == 400
        assert "cannot deactivate" in res.json().get("error", "").lower()

        # Via PATCH
        res_patch = api_client.patch(url, {"is_active": False}, format="json")
        assert res_patch.status_code == 400
        assert "cannot deactivate" in res_patch.json().get("error", "").lower()
