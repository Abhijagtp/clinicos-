import pytest
from datetime import timedelta
from django.utils import timezone
from django.urls import reverse

from apps.accounts.models import User
from apps.clinics.models import Clinic, ClinicMembership
from apps.patients.models import Patient
from apps.appointments.models import Appointment
from apps.consultations.models import Consultation, ClinicalNote


@pytest.fixture
def receptionist_user(db, clinic):
    user = User.objects.create_user(
        email="receptionist@dentalcare.com",
        password="password123",
        first_name="Ravi",
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
def patient_a(db, clinic, staff_user):
    return Patient.objects.create(
        clinic=clinic,
        name="Ananya Rao",
        phone="919988776655",
        whatsapp_id="919988776655",
        created_by=staff_user,
    )


@pytest.fixture
def appointment_a(db, clinic, patient_a):
    now = timezone.now()
    return Appointment.objects.create(
        clinic=clinic,
        patient=patient_a,
        starts_at=now + timedelta(hours=1),
        ends_at=now + timedelta(hours=1, minutes=30),
        status=Appointment.Status.CONFIRMED,
        appointment_type="Root Canal Evaluation",
        booking_source="whatsapp",
    )


@pytest.mark.django_db
class TestConsultationsAPI:

    def test_unauthenticated_fails(self, api_client):
        url = reverse("consultation-list-create")
        response = api_client.get(url)
        assert response.status_code == 401

        response = api_client.post(url, {})
        assert response.status_code == 401

    def test_create_consultation_with_appointment(
        self, api_client, staff_user, patient_a, appointment_a
    ):
        api_client.force_authenticate(user=staff_user)
        url = reverse("consultation-list-create")

        payload = {
            "patient_id": str(patient_a.id),
            "appointment_id": str(appointment_a.id),
            "doctor_id": staff_user.id,
            "initial_notes": "Patient complains of severe throbbing toothache on lower right side for 3 days.",
        }

        response = api_client.post(url, payload, format="json")
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "in_progress"
        assert data["patient"]["id"] == str(patient_a.id)
        assert len(data["notes"]) == 1
        assert "severe throbbing toothache" in data["notes"][0]["subjective"]

        # Check that appointment transitioned to in_consultation
        appointment_a.refresh_from_db()
        assert appointment_a.status == Appointment.Status.IN_CONSULTATION

    def test_list_consultations_and_filter(
        self, api_client, clinic, staff_user, other_clinic, other_clinic_user
    ):
        p_a = Patient.objects.create(clinic=clinic, name="Priya Kumar", created_by=staff_user)
        c_a = Consultation.objects.create(
            clinic=clinic,
            patient=p_a,
            doctor=staff_user,
            status=Consultation.Status.IN_PROGRESS,
        )

        p_b = Patient.objects.create(clinic=other_clinic, name="Rahul Mehta", created_by=other_clinic_user)
        Consultation.objects.create(
            clinic=other_clinic,
            patient=p_b,
            doctor=other_clinic_user,
            status=Consultation.Status.IN_PROGRESS,
        )

        api_client.force_authenticate(user=staff_user)
        url = reverse("consultation-list-create")
        response = api_client.get(url)
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 1
        assert results[0]["id"] == str(c_a.id)
        assert results[0]["patient_name"] == "Priya Kumar"

    def test_update_consultation_status_and_complete(
        self, api_client, clinic, staff_user, patient_a, appointment_a
    ):
        consultation = Consultation.objects.create(
            clinic=clinic,
            patient=patient_a,
            appointment=appointment_a,
            doctor=staff_user,
            status=Consultation.Status.IN_PROGRESS,
        )

        api_client.force_authenticate(user=staff_user)
        url = reverse("consultation-detail", kwargs={"id": consultation.id})

        response = api_client.patch(url, {"status": "completed"}, format="json")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["completed_at"] is not None

        # Verify linked appointment also completed
        appointment_a.refresh_from_db()
        assert appointment_a.status == Appointment.Status.COMPLETED

    def test_save_soap_notes(self, api_client, clinic, staff_user, patient_a):
        consultation = Consultation.objects.create(
            clinic=clinic,
            patient=patient_a,
            doctor=staff_user,
            status=Consultation.Status.IN_PROGRESS,
        )

        api_client.force_authenticate(user=staff_user)
        url = reverse("consultation-notes-update", kwargs={"id": consultation.id})

        soap_payload = {
            "subjective": "Patient reports pain when drinking cold water.",
            "objective": "Deep occlusal caries on tooth #19, percussion sensitive.",
            "assessment": "Irreversible pulpitis #19.",
            "plan": "Endodontic therapy (Root Canal Treatment) scheduled for next week.",
        }

        response = api_client.post(url, soap_payload, format="json")
        assert response.status_code == 200
        note_data = response.json()
        assert note_data["subjective"] == soap_payload["subjective"]
        assert note_data["objective"] == soap_payload["objective"]
        assert note_data["status"] == "draft"

    def test_finalize_note_doctor_required(
        self, api_client, clinic, staff_user, receptionist_user, patient_a
    ):
        consultation = Consultation.objects.create(
            clinic=clinic,
            patient=patient_a,
            doctor=staff_user,
            status=Consultation.Status.IN_PROGRESS,
        )
        note = ClinicalNote.objects.create(
            clinic=clinic,
            consultation=consultation,
            status=ClinicalNote.Status.DRAFT,
            subjective="Toothache on lower molar",
            objective="Deep decay",
            assessment="Pulpitis",
            plan="RCT",
            created_by=staff_user,
        )

        finalize_url = reverse(
            "consultation-note-finalize",
            kwargs={"id": consultation.id, "note_id": note.id},
        )

        # 1. Receptionist attempt should be rejected with 403 Forbidden
        api_client.force_authenticate(user=receptionist_user)
        resp_receptionist = api_client.post(finalize_url)
        assert resp_receptionist.status_code == 403
        assert "Only registered doctors" in resp_receptionist.json()["detail"]

        # 2. Doctor sign-off should succeed
        api_client.force_authenticate(user=staff_user)
        resp_doctor = api_client.post(finalize_url)
        assert resp_doctor.status_code == 200
        data = resp_doctor.json()
        assert data["status"] == "finalized"
        assert data["finalized_by_name"] is not None

        # 3. Subsequent edit attempt on finalized note should fail
        update_url = reverse("consultation-notes-update", kwargs={"id": consultation.id})
        resp_edit = api_client.post(update_url, {"subjective": "Altered"}, format="json")
        assert resp_edit.status_code == 400

    def test_ai_generate_soap_endpoint(self, api_client, clinic, staff_user, patient_a):
        consultation = Consultation.objects.create(
            clinic=clinic,
            patient=patient_a,
            doctor=staff_user,
            status=Consultation.Status.IN_PROGRESS,
        )

        api_client.force_authenticate(user=staff_user)
        url = reverse("consultation-generate-soap", kwargs={"id": consultation.id})

        payload = {
            "transcript": "Patient feels severe throbbing pain on tooth #30. Exam shows deep caries and percussion tenderness. Diagnosis is acute apical periodontitis. Plan is start root canal today and prescribe amoxicillin."
        }

        response = api_client.post(url, payload, format="json")
        assert response.status_code == 200
        data = response.json()
        assert "subjective" in data
        assert "objective" in data
        assert "assessment" in data
        assert "plan" in data
