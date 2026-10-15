import pytest
from datetime import timedelta
from django.utils import timezone
from django.urls import reverse
from apps.appointments.models import Appointment
from apps.accounts.models import User
from apps.clinics.models import ClinicMembership


@pytest.mark.django_db
class TestAppointmentAPIs:

    def test_health_check_endpoint(self, api_client):
        url = reverse("health-check")
        response = api_client.get(url)
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_availability_endpoint(self, api_client, clinic):
        url = reverse("appointments-availability")
        response = api_client.get(url, {"clinic_id": str(clinic.id), "date": "tomorrow"})
        assert response.status_code == 200
        data = response.json()
        assert "available_slots" in data
        assert data["timezone"] == "Asia/Kolkata"

    def test_list_appointments_isolated_to_staff_clinic(
        self, api_client, clinic, other_clinic, staff_user, other_clinic_user, patient
    ):
        start_at = timezone.now() + timedelta(days=2)
        end_at = start_at + timedelta(minutes=30)

        # Create appointment in clinic A
        appt_a = Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=start_at,
            ends_at=end_at,
            status=Appointment.Status.CONFIRMED,
        )

        # Create appointment in clinic B
        other_patient = patient
        appt_b = Appointment.objects.create(
            clinic=other_clinic,
            patient=other_patient,
            starts_at=start_at,
            ends_at=end_at,
            status=Appointment.Status.CONFIRMED,
        )

        url = reverse("appointments-list-create")

        # Staff user from clinic A logs in
        api_client.force_authenticate(user=staff_user)
        response_a = api_client.get(url)
        assert response_a.status_code == 200
        data_a = response_a.json()
        ids_a = [item["id"] for item in data_a]
        assert str(appt_a.id) in ids_a
        # Proves Clinic A staff CANNOT see Clinic B appointments
        assert str(appt_b.id) not in ids_a

        # Attempt to explicitly filter for other clinic
        response_cross = api_client.get(url, {"clinic_id": str(other_clinic.id)})
        assert response_cross.status_code == 403

    def test_reschedule_appointment_endpoint(self, api_client, clinic, staff_user, patient):
        start_at = timezone.now() + timedelta(days=2)
        end_at = start_at + timedelta(minutes=30)
        appt = Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=start_at,
            ends_at=end_at,
            status=Appointment.Status.CONFIRMED,
        )

        api_client.force_authenticate(user=staff_user)
        url = reverse("appointment-reschedule", kwargs={"id": appt.id})

        new_start = start_at + timedelta(days=1)
        new_end = end_at + timedelta(days=1)

        response = api_client.post(url, {
            "new_starts_at": new_start.isoformat(),
            "new_ends_at": new_end.isoformat(),
        }, format="json")

        assert response.status_code == 200
        appt.refresh_from_db()
        assert appt.starts_at == new_start

    def test_cancel_appointment_endpoint(self, api_client, clinic, staff_user, patient):
        start_at = timezone.now() + timedelta(days=2)
        end_at = start_at + timedelta(minutes=30)
        appt = Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=start_at,
            ends_at=end_at,
            status=Appointment.Status.CONFIRMED,
        )

        api_client.force_authenticate(user=staff_user)
        url = reverse("appointment-cancel", kwargs={"id": appt.id})

        response = api_client.post(url, {"reason": "Cancelled per doctor schedule"}, format="json")
        assert response.status_code == 200
        appt.refresh_from_db()
        assert appt.status == Appointment.Status.CANCELLED

    def test_receptionist_can_check_in_appointment(self, api_client, clinic, patient):
        receptionist = User.objects.create_user(
            email="reception@dentalcare.com",
            password="password123",
            first_name="Anita",
        )
        ClinicMembership.objects.create(
            clinic=clinic,
            user=receptionist,
            role=ClinicMembership.Role.RECEPTIONIST,
            is_active=True,
        )

        start_at = timezone.now() + timedelta(hours=1)
        end_at = start_at + timedelta(minutes=30)
        appt = Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=start_at,
            ends_at=end_at,
            status=Appointment.Status.CONFIRMED,
        )

        api_client.force_authenticate(user=receptionist)
        url = reverse("appointment-check-in", kwargs={"id": appt.id})

        response = api_client.post(url)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "checked_in"
        assert data["time_formatted"] is not None
        assert data["time_range"] is not None

        appt.refresh_from_db()
        assert appt.status == Appointment.Status.CHECKED_IN

    def test_appointment_status_update_cycle(self, api_client, clinic, staff_user, patient):
        appt = Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=timezone.now() + timedelta(hours=2),
            ends_at=timezone.now() + timedelta(hours=2, minutes=30),
            status=Appointment.Status.CONFIRMED,
        )

        api_client.force_authenticate(user=staff_user)
        url = reverse("appointment-status-update", kwargs={"id": appt.id})

        # Transition to in_consultation
        res = api_client.post(url, {"status": "in_consultation"}, format="json")
        assert res.status_code == 200
        assert res.json()["status"] == "in_consultation"

        # Transition to completed
        res_comp = api_client.post(url, {"status": "completed"}, format="json")
        assert res_comp.status_code == 200
        assert res_comp.json()["status"] == "completed"

    def test_appointment_list_filter_today_and_tomorrow(self, api_client, clinic, staff_user, patient):
        from zoneinfo import ZoneInfo
        from datetime import datetime
        tz = ZoneInfo(clinic.timezone)
        today_local = datetime.now(tz).date()

        # Appointment today
        appt_today = Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )
        # Appointment tomorrow
        appt_tomorrow = Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, minutes=30),
            status=Appointment.Status.CONFIRMED,
        )

        api_client.force_authenticate(user=staff_user)
        url = reverse("appointments-list-create")

        # Query today
        res_today = api_client.get(url, {"date": "today"})
        assert res_today.status_code == 200
        today_ids = [a["id"] for a in res_today.json()]
        assert str(appt_today.id) in today_ids
        assert str(appt_tomorrow.id) not in today_ids

        # Query tomorrow
        res_tomorrow = api_client.get(url, {"date": "tomorrow"})
        assert res_tomorrow.status_code == 200
        tomorrow_ids = [a["id"] for a in res_tomorrow.json()]
        assert str(appt_tomorrow.id) in tomorrow_ids
        assert str(appt_today.id) not in tomorrow_ids

