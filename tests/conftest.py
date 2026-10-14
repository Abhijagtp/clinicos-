import pytest
import hmac
import hashlib
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.clinics.models import Clinic, ClinicMembership
from apps.patients.models import Patient
from apps.appointments.models import Appointment
from apps.integrations.google_calendar.service import MockCalendarService
from apps.ai_assistant.service import MockAIAssistant


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def clinic(db):
    return Clinic.objects.create(
        name="Dental Care Bengaluru",
        timezone="Asia/Kolkata",
        phone_number="+919876543210",
        default_appointment_duration=30,
        slot_buffer_minutes=0,
        min_notice_hours=1,
    )


@pytest.fixture
def other_clinic(db):
    return Clinic.objects.create(
        name="Smile Dental Mumbai",
        timezone="Asia/Kolkata",
        phone_number="+919123456789",
    )


@pytest.fixture
def staff_user(db, clinic):
    user = User.objects.create_user(
        email="doctor@dentalcare.com",
        password="secretpassword123",
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
def other_clinic_user(db, other_clinic):
    user = User.objects.create_user(
        email="other@smiledental.com",
        password="secretpassword123",
        first_name="Dr. Rahul",
        last_name="Verma",
    )
    ClinicMembership.objects.create(
        clinic=other_clinic,
        user=user,
        role=ClinicMembership.Role.DOCTOR,
        is_active=True,
    )
    return user


@pytest.fixture
def patient(db, clinic, staff_user):
    return Patient.objects.create(
        clinic=clinic,
        first_name="Aarav",
        last_name="Patel",
        whatsapp_id="919999988888",
        phone="919999988888",
        consent_status=True,
        created_by=staff_user,
    )


@pytest.fixture
def mock_calendar():
    return MockCalendarService()


@pytest.fixture
def mock_ai():
    return MockAIAssistant()


def generate_meta_signature(payload_bytes: bytes, secret: str = "mock_app_secret_test") -> str:
    signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={signature}"
