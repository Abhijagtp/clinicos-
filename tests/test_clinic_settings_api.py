import pytest
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from django.urls import reverse

from apps.accounts.models import User
from apps.clinics.models import Clinic, ClinicMembership
from apps.integrations.google_calendar.service import BaseAvailabilityCalculator


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
class TestClinicSettingsAPI:

    def test_clinic_settings_unauthenticated_fails(self, api_client):
        url = reverse("clinic-settings")
        response = api_client.get(url)
        assert response.status_code == 401

    def test_get_clinic_settings_success(self, api_client, clinic, staff_user):
        url = reverse("clinic-settings")
        api_client.force_authenticate(user=staff_user)
        response = api_client.get(url)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(clinic.id)
        assert data["name"] == clinic.name
        assert data["timezone"] == "Asia/Kolkata"
        assert data["default_appointment_duration"] == 30
        assert data["slot_buffer_minutes"] == 0
        assert data["min_notice_hours"] == 1
        assert "clinic_hours" in data
        assert data["user_role"] == "doctor"
        assert data["can_edit_settings"] is True

    def test_patch_clinic_settings_success(self, api_client, clinic, staff_user):
        url = reverse("clinic-settings")
        api_client.force_authenticate(user=staff_user)

        payload = {
            "name": "Clinic OS Premier Bengaluru",
            "default_appointment_duration": 45,
            "slot_buffer_minutes": 15,
            "min_notice_hours": 2,
        }
        response = api_client.patch(url, payload, format="json")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Clinic OS Premier Bengaluru"
        assert data["default_appointment_duration"] == 45
        assert data["slot_buffer_minutes"] == 15
        assert data["min_notice_hours"] == 2

        # Verify DB persisted
        clinic.refresh_from_db()
        assert clinic.name == "Clinic OS Premier Bengaluru"
        assert clinic.default_appointment_duration == 45
        assert clinic.slot_buffer_minutes == 15
        assert clinic.min_notice_hours == 2

    def test_patch_operating_hours(self, api_client, clinic, staff_user):
        url = reverse("clinic-settings")
        api_client.force_authenticate(user=staff_user)

        new_hours = {
            "0": [{"start": "10:00", "end": "14:00"}],
            "1": [{"start": "10:00", "end": "14:00"}],
            "2": [{"start": "10:00", "end": "14:00"}],
            "3": [{"start": "10:00", "end": "14:00"}],
            "4": [{"start": "10:00", "end": "14:00"}],
            "5": [{"start": "10:00", "end": "12:00"}],
            "6": [],
        }
        response = api_client.patch(url, {"clinic_hours": new_hours}, format="json")

        assert response.status_code == 200
        clinic.refresh_from_db()
        assert clinic.clinic_hours["0"] == [{"start": "10:00", "end": "14:00"}]

    def test_patch_clinic_settings_invalid_timezone(self, api_client, clinic, staff_user):
        url = reverse("clinic-settings")
        api_client.force_authenticate(user=staff_user)

        response = api_client.patch(url, {"timezone": "Invalid/Fake_Zone"}, format="json")
        assert response.status_code == 400
        assert "timezone" in response.json()

    def test_patch_clinic_settings_receptionist_forbidden(self, api_client, receptionist_user):
        url = reverse("clinic-settings")
        api_client.force_authenticate(user=receptionist_user)

        response = api_client.patch(url, {"default_appointment_duration": 60}, format="json")
        assert response.status_code == 403
        assert "permission" in response.json().get("detail", "").lower()

    def test_availability_service_reflects_updated_settings(self, api_client, clinic, staff_user):
        """Updating clinic hours immediately updates the available slots in calculate_available_slots."""
        url = reverse("clinic-settings")
        api_client.force_authenticate(user=staff_user)

        # Set Saturday ("5") hours to a single 10:00-11:00 window with 30-min duration
        custom_hours = {
            "0": [], "1": [], "2": [], "3": [], "4": [],
            "5": [{"start": "10:00", "end": "11:00"}],
            "6": []
        }
        response = api_client.patch(url, {
            "clinic_hours": custom_hours,
            "default_appointment_duration": 30,
            "slot_buffer_minutes": 0,
        }, format="json")
        assert response.status_code == 200

        clinic.refresh_from_db()
        # Find the next Saturday
        tz = ZoneInfo(clinic.timezone)
        today = datetime.now(tz).date()
        days_ahead = (5 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        saturday = today + timedelta(days=days_ahead)

        slots = BaseAvailabilityCalculator.calculate_slots(
            clinic=clinic,
            target_date=saturday,
            busy_intervals=[],
            duration_minutes=clinic.default_appointment_duration,
        )
        # 10:00 - 11:00 with 30-min duration produces exactly 2 slots: 10:00 and 10:30
        assert len(slots) == 2
        assert "10:00 AM" in slots[0]["time_formatted"]
        assert "10:30 AM" in slots[1]["time_formatted"]
