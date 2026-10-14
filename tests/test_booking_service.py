import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone
from apps.appointments.models import Appointment, BookingReservation
from apps.audit.models import AuditEvent
from services.booking_service import BookingService, BookingConflictError, CalendarError


@pytest.mark.django_db
class TestBookingService:

    def test_confirm_booking_creates_appointment_and_calendar_event(self, clinic, patient, mock_calendar):
        service = BookingService(calendar_service=mock_calendar)
        start_at = timezone.now() + timedelta(days=2)
        end_at = start_at + timedelta(minutes=30)

        appt = service.confirm_booking(
            clinic=clinic,
            patient=patient,
            start_at=start_at,
            end_at=end_at,
            appointment_type="dental_cleaning",
        )

        assert appt.id is not None
        assert appt.status == Appointment.Status.CONFIRMED
        assert appt.external_calendar_event_id != ""
        assert appt.external_calendar_event_id in mock_calendar.events

        # Check AuditEvent was created
        audit = AuditEvent.objects.filter(object_id=str(appt.id), action="appointment_created").first()
        assert audit is not None
        assert audit.clinic == clinic

    def test_prevent_double_booking_same_slot(self, clinic, patient, mock_calendar):
        service = BookingService(calendar_service=mock_calendar)
        start_at = timezone.now() + timedelta(days=3)
        end_at = start_at + timedelta(minutes=30)

        # First booking succeeds
        service.confirm_booking(
            clinic=clinic,
            patient=patient,
            start_at=start_at,
            end_at=end_at,
        )

        # Second booking for the exact same slot raises BookingConflictError
        with pytest.raises(BookingConflictError):
            service.confirm_booking(
                clinic=clinic,
                patient=patient,
                start_at=start_at,
                end_at=end_at,
            )

    def test_calendar_failure_triggers_safe_compensation(self, clinic, patient):
        failing_calendar = MagicMock()
        failing_calendar.create_event.side_effect = CalendarError("External calendar API down")

        service = BookingService(calendar_service=failing_calendar)
        start_at = timezone.now() + timedelta(days=4)
        end_at = start_at + timedelta(minutes=30)

        with pytest.raises(CalendarError):
            service.confirm_booking(
                clinic=clinic,
                patient=patient,
                start_at=start_at,
                end_at=end_at,
            )

        # Ensure no appointment was persisted in database
        assert not Appointment.objects.filter(clinic=clinic, starts_at=start_at).exists()
        # Ensure reservation was released
        reservation = BookingReservation.objects.filter(clinic=clinic, starts_at=start_at).first()
        assert reservation.status == BookingReservation.Status.RELEASED

    def test_reschedule_appointment(self, clinic, patient, mock_calendar):
        service = BookingService(calendar_service=mock_calendar)
        start_at = timezone.now() + timedelta(days=2)
        end_at = start_at + timedelta(minutes=30)

        appt = service.confirm_booking(clinic, patient, start_at, end_at)

        new_start = start_at + timedelta(days=1)
        new_end = end_at + timedelta(days=1)

        updated = service.reschedule_appointment(clinic, appt, new_start, new_end)
        assert updated.starts_at == new_start
        assert mock_calendar.events[appt.external_calendar_event_id]["start"] == new_start
        assert mock_calendar.events[appt.external_calendar_event_id]["attendee_email"] == patient.email

        # Audit event recorded
        assert AuditEvent.objects.filter(object_id=str(appt.id), action="appointment_rescheduled").exists()

    def test_reschedule_appointment_creates_calendar_event_if_originally_missing(self, clinic, patient, mock_calendar):
        service = BookingService(calendar_service=mock_calendar)
        start_at = timezone.now() + timedelta(days=2)
        end_at = start_at + timedelta(minutes=30)

        # Legacy appointment without calendar event id
        appt = Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=start_at,
            ends_at=end_at,
            status=Appointment.Status.CONFIRMED,
            external_calendar_event_id="",
        )

        new_start = start_at + timedelta(days=1)
        new_end = end_at + timedelta(days=1)

        updated = service.reschedule_appointment(clinic, appt, new_start, new_end)
        assert updated.starts_at == new_start
        assert updated.external_calendar_event_id != ""
        assert updated.external_calendar_event_id in mock_calendar.events
        assert mock_calendar.events[updated.external_calendar_event_id]["attendee_email"] == patient.email

    def test_cancel_appointment(self, clinic, patient, mock_calendar):
        service = BookingService(calendar_service=mock_calendar)
        start_at = timezone.now() + timedelta(days=2)
        end_at = start_at + timedelta(minutes=30)

        appt = service.confirm_booking(clinic, patient, start_at, end_at)
        cal_id = appt.external_calendar_event_id

        cancelled = service.cancel_appointment(clinic, appt, reason="Patient travelling")
        assert cancelled.status == Appointment.Status.CANCELLED
        assert cal_id not in mock_calendar.events

        # Audit event recorded
        assert AuditEvent.objects.filter(object_id=str(appt.id), action="appointment_cancelled").exists()
