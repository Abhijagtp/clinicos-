import pytest
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from django.utils import timezone
from django.urls import reverse

from apps.appointments.models import Appointment
from apps.patients.models import Patient
from apps.conversations.models import Conversation, ConversationState


@pytest.mark.django_db
class TestDashboardStatsAPI:

    def test_dashboard_stats_requires_authentication(self, api_client):
        url = reverse("dashboard-stats")
        response = api_client.get(url)
        assert response.status_code == 401

    def test_dashboard_stats_returns_clinic_metrics(self, api_client, clinic, staff_user):
        tz = ZoneInfo(clinic.timezone)
        now_local = datetime.now(tz)
        today_date = now_local.date()

        # Create patient
        patient = Patient.objects.create(
            clinic=clinic,
            first_name="Anita",
            last_name="Desai",
            phone="+919876543210",
        )

        # Create an appointment for today
        appt_time = datetime.combine(today_date, time(14, 0), tzinfo=tz)
        Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=appt_time,
            ends_at=appt_time + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )

        # Create a conversation
        Conversation.objects.create(
            clinic=clinic,
            patient=patient,
            whatsapp_sender_id="+919876543210",
            current_state=ConversationState.OFFERING_SLOTS,
        )

        url = reverse("dashboard-stats")
        api_client.force_authenticate(user=staff_user)
        response = api_client.get(url)

        assert response.status_code == 200
        data = response.json()

        assert "clinic" in data
        assert data["clinic"]["name"] == clinic.name
        assert data["date"] == today_date.isoformat()

        # Today stats
        assert data["today"]["total"] >= 1
        assert data["today"]["confirmed"] >= 1

        # Patients
        assert data["patients"]["total_registered"] >= 1

        # Conversations
        assert data["conversations"]["booking_in_progress"] >= 1
        assert data["conversations"]["ai_assistant_status"] == "active"

        # Calendar
        assert data["calendar"]["status"] == "connected"

    def test_dashboard_stats_custom_date_filter(self, api_client, clinic, staff_user):
        tz = ZoneInfo(clinic.timezone)
        target_date = (datetime.now(tz) + timedelta(days=3)).date()

        patient = Patient.objects.create(
            clinic=clinic,
            first_name="Rohan",
            last_name="Mehta",
            phone="+919999888877",
        )

        appt_time = datetime.combine(target_date, time(11, 0), tzinfo=tz)
        Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=appt_time,
            ends_at=appt_time + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )

        url = reverse("dashboard-stats")
        api_client.force_authenticate(user=staff_user)
        response = api_client.get(url, {"date": target_date.isoformat()})

        assert response.status_code == 200
        data = response.json()
        assert data["date"] == target_date.isoformat()
        assert data["today"]["confirmed"] >= 1

    def test_dashboard_stats_clinic_isolation(
        self, api_client, clinic, other_clinic, staff_user, other_clinic_user
    ):
        tz = ZoneInfo(clinic.timezone)
        today = datetime.now(tz).date()

        # Patient & appt in other_clinic
        other_patient = Patient.objects.create(
            clinic=other_clinic,
            first_name="Other",
            last_name="Patient",
            phone="+919111222333",
        )
        appt_time = datetime.combine(today, time(16, 0), tzinfo=tz)
        Appointment.objects.create(
            clinic=other_clinic,
            patient=other_patient,
            starts_at=appt_time,
            ends_at=appt_time + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )

        url = reverse("dashboard-stats")
        api_client.force_authenticate(user=staff_user)
        response = api_client.get(url)

        assert response.status_code == 200
        data = response.json()
        assert data["clinic"]["id"] == str(clinic.id)
        # Staff in clinic A does not see appointments from other_clinic
        assert data["today"]["total"] == 0
