import pytest
from datetime import timedelta
from django.utils import timezone
from django.urls import reverse

from apps.accounts.models import User
from apps.clinics.models import Clinic, ClinicMembership
from apps.patients.models import Patient
from apps.appointments.models import Appointment
from apps.consultations.models import Consultation, ClinicalNote


@pytest.mark.django_db
class TestPatientsAPI:

    def test_patients_unauthenticated_fails(self, api_client):
        url = reverse("patient-list-create")
        response = api_client.get(url)
        assert response.status_code == 401

        response = api_client.post(url, {"name": "Test Patient"})
        assert response.status_code == 401

    def test_list_patients_clinic_isolation(
        self, api_client, clinic, other_clinic, staff_user, other_clinic_user
    ):
        # Patient in clinic A
        p_a = Patient.objects.create(
            clinic=clinic,
            name="Aarav Sharma",
            phone="919999988888",
            whatsapp_id="919999988888",
            created_by=staff_user,
        )

        # Patient in clinic B
        p_b = Patient.objects.create(
            clinic=other_clinic,
            name="Bhavna Desai",
            phone="919888877777",
            whatsapp_id="919888877777",
            created_by=other_clinic_user,
        )

        url = reverse("patient-list-create")
        api_client.force_authenticate(user=staff_user)

        response = api_client.get(url)
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        results = data["results"]
        ids = [item["id"] for item in results]
        assert str(p_a.id) in ids
        assert str(p_b.id) not in ids

        # Attempt to explicitly filter for Clinic B should be rejected
        response_other = api_client.get(url, {"clinic_id": str(other_clinic.id)})
        assert response_other.status_code == 403

    def test_search_patients(self, api_client, clinic, staff_user):
        p1 = Patient.objects.create(
            clinic=clinic,
            name="Rohan Verma",
            phone="919123400001",
            whatsapp_id="919123400001",
            email="rohan@example.com",
            created_by=staff_user,
        )
        p2 = Patient.objects.create(
            clinic=clinic,
            name="Sneha Kulkarni",
            phone="919123400002",
            whatsapp_id="919123400002",
            email="sneha@example.com",
            created_by=staff_user,
        )

        url = reverse("patient-list-create")
        api_client.force_authenticate(user=staff_user)

        # Search by name substring
        res = api_client.get(url, {"search": "Sneha"})
        assert res.status_code == 200
        data = res.json()
        assert data["count"] == 1
        assert data["results"][0]["id"] == str(p2.id)

        # Search by phone substring
        res_phone = api_client.get(url, {"search": "00001"})
        assert res_phone.status_code == 200
        assert res_phone.json()["count"] == 1
        assert res_phone.json()["results"][0]["id"] == str(p1.id)

        # Search by patient_number
        res_number = api_client.get(url, {"search": p1.patient_number})
        assert res_number.status_code == 200
        assert res_number.json()["count"] == 1
        assert res_number.json()["results"][0]["id"] == str(p1.id)

    def test_create_patient_success(self, api_client, clinic, staff_user):
        url = reverse("patient-list-create")
        api_client.force_authenticate(user=staff_user)

        payload = {
            "first_name": "Vikram",
            "last_name": "Aditya",
            "phone": "+91 (987) 654-3210",
            "whatsapp_id": "919876543210",
            "email": "vikram.aditya@gmail.com",
            "sex": "male",
            "notes": "Prefers morning appointments",
            "consent_status": True,
        }

        response = api_client.post(url, payload, format="json")
        assert response.status_code == 201
        data = response.json()
        assert data["first_name"] == "Vikram"
        assert data["last_name"] == "Aditya"
        assert data["display_name"] == "Vikram Aditya"
        assert data["phone"] == "919876543210"  # sanitized
        assert data["patient_number"].startswith("PT-")
        assert data["clinic_id"] == str(clinic.id)

        # Verify DB
        created_patient = Patient.objects.get(id=data["id"])
        assert created_patient.created_by == staff_user
        assert created_patient.clinic == clinic
        assert created_patient.notes == "Prefers morning appointments"

    def test_get_patient_detail_with_stats(self, api_client, clinic, staff_user, patient):
        # Create past and upcoming appointments
        now = timezone.now()
        Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=now - timedelta(days=7),
            ends_at=now - timedelta(days=7, minutes=-30),
            status=Appointment.Status.COMPLETED,
        )
        Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=now + timedelta(days=3),
            ends_at=now + timedelta(days=3, minutes=30),
            status=Appointment.Status.CONFIRMED,
        )

        url = reverse("patient-detail", kwargs={"id": patient.id})
        api_client.force_authenticate(user=staff_user)

        response = api_client.get(url)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(patient.id)
        assert data["total_appointments"] == 2
        assert data["last_visit_at"] is not None
        assert data["upcoming_appointment_at"] is not None
        assert len(data["recent_appointments"]) == 2

    def test_patch_patient_success(self, api_client, staff_user, patient):
        url = reverse("patient-detail", kwargs={"id": patient.id})
        api_client.force_authenticate(user=staff_user)

        payload = {
            "notes": "Patient has mild dental anxiety. Pre-medication required.",
            "email": "updated.aarav@domain.com",
            "sex": "male",
        }

        response = api_client.patch(url, payload, format="json")
        assert response.status_code == 200
        data = response.json()
        assert data["notes"] == "Patient has mild dental anxiety. Pre-medication required."
        assert data["email"] == "updated.aarav@domain.com"
        assert data["sex"] == "male"

        patient.refresh_from_db()
        assert patient.notes == "Patient has mild dental anxiety. Pre-medication required."
        assert patient.email == "updated.aarav@domain.com"

    def test_cross_clinic_access_forbidden(
        self, api_client, other_clinic, other_clinic_user, patient
    ):
        # Patient belongs to 'clinic', user belongs to 'other_clinic'
        url = reverse("patient-detail", kwargs={"id": patient.id})
        api_client.force_authenticate(user=other_clinic_user)

        # GET should be rejected with 403 Forbidden
        response = api_client.get(url)
        assert response.status_code == 403

        # PATCH should also be rejected
        patch_res = api_client.patch(url, {"notes": "Hacked"})
        assert patch_res.status_code == 403

    def test_soft_delete_patient(self, api_client, staff_user, patient):
        url = reverse("patient-detail", kwargs={"id": patient.id})
        api_client.force_authenticate(user=staff_user)

        response = api_client.delete(url)
        assert response.status_code == 200
        assert response.json()["is_active"] is False

        patient.refresh_from_db()
        assert patient.is_active is False

    def test_patient_timeline(self, api_client, clinic, staff_user, patient):
        now = timezone.now()
        # Add appointment
        appt = Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(days=2, minutes=-30),
            status=Appointment.Status.COMPLETED,
            appointment_type="Root Canal Consultation",
        )

        # Add consultation & note
        consult = Consultation.objects.create(
            clinic=clinic,
            patient=patient,
            appointment=appt,
            doctor=staff_user,
            status=Consultation.Status.COMPLETED,
        )
        ClinicalNote.objects.create(
            clinic=clinic,
            consultation=consult,
            subjective="Severe sensitivity to cold",
            objective="Tooth #19 deep caries",
            assessment="Irreversible pulpitis",
            plan="Root canal therapy scheduled",
            created_by=staff_user,
        )

        url = reverse("patient-timeline", kwargs={"id": patient.id})
        api_client.force_authenticate(user=staff_user)

        response = api_client.get(url)
        assert response.status_code == 200
        data = response.json()
        assert data["patient_id"] == str(patient.id)
        timeline = data["timeline"]
        assert len(timeline) >= 2

        types = [item["type"] for item in timeline]
        assert "appointment" in types
        assert "consultation" in types
