import pytest
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone
from apps.appointments.models import Appointment, BookingReservation
from apps.integrations.google_calendar.service import (
    BaseAvailabilityCalculator,
    MockCalendarService,
    parse_relative_date,
)


@pytest.mark.django_db
class TestCalendarAvailability:

    def test_relative_date_parsing(self, clinic):
        tz = ZoneInfo(clinic.timezone)
        today = datetime.now(tz).date()

        assert parse_relative_date("today", clinic.timezone) == today
        assert parse_relative_date("tomorrow", clinic.timezone) == today + timedelta(days=1)
        assert parse_relative_date("2026-12-25", clinic.timezone) == date(2026, 12, 25)

    def test_calculate_slots_respects_clinic_hours(self, clinic):
        # Target a future Monday
        tz = ZoneInfo(clinic.timezone)
        future_date = datetime.now(tz).date() + timedelta(days=7)
        while future_date.weekday() != 0:  # Ensure it is Monday
            future_date += timedelta(days=1)

        slots = BaseAvailabilityCalculator.calculate_slots(
            clinic=clinic,
            target_date=future_date,
            busy_intervals=[],
            duration_minutes=30,
        )

        assert len(slots) > 0
        # Clinic schedule is 9am-1pm (4 hrs = 8 slots) and 2pm-7pm (5 hrs = 10 slots) -> 18 slots
        assert any("09:00 AM" in s["time_formatted"] for s in slots)
        assert any("06:30 PM" in s["time_formatted"] for s in slots)

    def test_busy_interval_excludes_overlapping_slot(self, clinic, patient):
        tz = ZoneInfo(clinic.timezone)
        target_date = datetime.now(tz).date() + timedelta(days=5)
        while target_date.weekday() != 1:  # Tuesday
            target_date += timedelta(days=1)

        # Create a confirmed appointment at 10:00 AM - 10:30 AM
        start_dt = datetime.combine(target_date, datetime.min.time(), tzinfo=tz).replace(hour=10, minute=0)
        end_dt = start_dt + timedelta(minutes=30)

        Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            starts_at=start_dt,
            ends_at=end_dt,
            status=Appointment.Status.CONFIRMED,
        )

        slots = BaseAvailabilityCalculator.calculate_slots(
            clinic=clinic,
            target_date=target_date,
            busy_intervals=[],
            duration_minutes=30,
        )

        slot_times = [s["time_formatted"] for s in slots]
        assert "10:00 AM" not in slot_times
        assert "10:30 AM" in slot_times

    def test_mock_calendar_event_lifecycle(self, clinic):
        cal = MockCalendarService()
        tz = ZoneInfo(clinic.timezone)
        start_dt = timezone.now() + timedelta(days=2)
        end_dt = start_dt + timedelta(minutes=30)

        event_id = cal.create_event(
            clinic=clinic,
            summary="Dental Checkup",
            start_at=start_dt,
            end_at=end_dt,
            idempotency_key="unique_key_1",
        )
        assert event_id is not None
        assert event_id in cal.events

        # Test idempotency
        duplicate_id = cal.create_event(
            clinic=clinic,
            summary="Dental Checkup",
            start_at=start_dt,
            end_at=end_dt,
            idempotency_key="unique_key_1",
        )
        assert duplicate_id == event_id

        # Update event
        new_start = start_dt + timedelta(hours=1)
        new_end = end_dt + timedelta(hours=1)
        assert cal.update_event(clinic, event_id, new_start, new_end) is True
        assert cal.events[event_id]["start"] == new_start

        # Delete event
        assert cal.delete_event(clinic, event_id) is True
        assert event_id not in cal.events
