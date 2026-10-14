import pytest
from datetime import timedelta
from django.utils import timezone
from django.urls import reverse
from apps.appointments.models import Appointment


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
