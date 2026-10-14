import logging
from abc import ABC, abstractmethod
from datetime import datetime, date, time, timedelta
from typing import List, Dict, Any, Optional
from zoneinfo import ZoneInfo
from django.conf import settings
from django.utils import timezone
from apps.clinics.models import Clinic
from apps.appointments.models import Appointment, BookingReservation
from .models import GoogleCalendarConfig

logger = logging.getLogger(__name__)


class CalendarError(Exception):
    """Base exception for calendar errors."""
    pass


class CalendarServiceInterface(ABC):
    @abstractmethod
    def get_available_slots(
        self,
        clinic: Clinic,
        target_date: date,
        duration_minutes: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Calculates free slots based on clinic operating schedule and existing busy periods."""
        pass

    @abstractmethod
    def create_event(
        self,
        clinic: Clinic,
        summary: str,
        start_at: datetime,
        end_at: datetime,
        description: str = "",
        idempotency_key: Optional[str] = None,
        attendee_email: Optional[str] = None,
        attendee_name: Optional[str] = None,
    ) -> str:
        """Creates calendar event with optional attendee and returns external event ID."""
        pass

    @abstractmethod
    def update_event(
        self,
        clinic: Clinic,
        event_id: str,
        start_at: datetime,
        end_at: datetime,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        attendee_email: Optional[str] = None,
        attendee_name: Optional[str] = None,
    ) -> bool:
        """Reschedules an existing calendar event and updates attendees."""
        pass

    @abstractmethod
    def delete_event(
        self,
        clinic: Clinic,
        event_id: str,
    ) -> bool:
        """Deletes/cancels a calendar event."""
        pass


def parse_relative_date(date_expr: str, tz_name: str = "Asia/Kolkata") -> Optional[date]:
    """
    Parses relative date strings ('today', 'tomorrow', 'next monday', or 'YYYY-MM-DD').
    """
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    expr = (date_expr or "").strip().lower()

    if expr == "today":
        return now_local.date()
    elif expr == "tomorrow":
        return (now_local + timedelta(days=1)).date()
    elif expr in ["day after tomorrow", "overmorrow"]:
        return (now_local + timedelta(days=2)).date()
    elif expr.startswith("next monday"):
        days_ahead = (0 - now_local.weekday() + 7) % 7
        days_ahead = 7 if days_ahead == 0 else days_ahead
        return (now_local + timedelta(days=days_ahead)).date()
    elif expr.startswith("next tuesday"):
        days_ahead = (1 - now_local.weekday() + 7) % 7
        days_ahead = 7 if days_ahead == 0 else days_ahead
        return (now_local + timedelta(days=days_ahead)).date()
    elif expr.startswith("next wednesday"):
        days_ahead = (2 - now_local.weekday() + 7) % 7
        days_ahead = 7 if days_ahead == 0 else days_ahead
        return (now_local + timedelta(days=days_ahead)).date()
    elif expr.startswith("next thursday"):
        days_ahead = (3 - now_local.weekday() + 7) % 7
        days_ahead = 7 if days_ahead == 0 else days_ahead
        return (now_local + timedelta(days=days_ahead)).date()
    elif expr.startswith("next friday"):
        days_ahead = (4 - now_local.weekday() + 7) % 7
        days_ahead = 7 if days_ahead == 0 else days_ahead
        return (now_local + timedelta(days=days_ahead)).date()
    elif expr.startswith("next saturday"):
        days_ahead = (5 - now_local.weekday() + 7) % 7
        days_ahead = 7 if days_ahead == 0 else days_ahead
        return (now_local + timedelta(days=days_ahead)).date()

    # Try ISO date parsing
    try:
        return datetime.strptime(expr, "%Y-%m-%d").date()
    except ValueError:
        pass

    return None


class BaseAvailabilityCalculator:
    """
    Calculates free slots given clinic schedule, database bookings, and external busy periods.
    """

    @staticmethod
    def calculate_slots(
        clinic: Clinic,
        target_date: date,
        busy_intervals: List[Dict[str, datetime]],
        duration_minutes: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        duration = duration_minutes or clinic.default_appointment_duration or 30
        buffer_min = clinic.slot_buffer_minutes or 0
        min_notice_hours = clinic.min_notice_hours or 1
        tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
        now_local = datetime.now(tz)

        # 1. Fetch clinic operating intervals for this day of week
        weekday_str = str(target_date.weekday())
        hours_config = clinic.clinic_hours or {}
        day_ranges = hours_config.get(weekday_str, [])
        if not day_ranges:
            # Closed on this day
            return []

        # 2. Collect DB busy intervals (active appointments + active reservations)
        day_start = datetime.combine(target_date, time.min, tzinfo=tz)
        day_end = datetime.combine(target_date, time.max, tzinfo=tz)

        db_appointments = Appointment.objects.filter(
            clinic=clinic,
            starts_at__lt=day_end,
            ends_at__gt=day_start,
        ).exclude(status__in=[Appointment.Status.CANCELLED, Appointment.Status.NO_SHOW])

        for appt in db_appointments:
            busy_intervals.append({
                "start": appt.starts_at.astimezone(tz),
                "end": appt.ends_at.astimezone(tz),
            })

        db_reservations = BookingReservation.objects.filter(
            clinic=clinic,
            status=BookingReservation.Status.PENDING,
            expires_at__gt=timezone.now(),
            starts_at__lt=day_end,
            ends_at__gt=day_start,
        )
        for res in db_reservations:
            busy_intervals.append({
                "start": res.starts_at.astimezone(tz),
                "end": res.ends_at.astimezone(tz),
            })

        # 3. Generate candidate slots
        earliest_allowed = now_local + timedelta(hours=min_notice_hours)
        available_slots = []

        for window in day_ranges:
            try:
                start_h, start_m = map(int, window["start"].split(":"))
                end_h, end_m = map(int, window["end"].split(":"))
            except (KeyError, ValueError):
                continue

            window_start = datetime.combine(target_date, time(start_h, start_m), tzinfo=tz)
            window_end = datetime.combine(target_date, time(end_h, end_m), tzinfo=tz)

            curr = window_start
            step = timedelta(minutes=duration + buffer_min)
            slot_delta = timedelta(minutes=duration)

            while curr + slot_delta <= window_end:
                slot_start = curr
                slot_end = curr + slot_delta

                # Skip if in the past or under notice window
                if slot_start >= earliest_allowed:
                    # Check collision with busy intervals
                    collides = False
                    for b in busy_intervals:
                        b_start = b["start"]
                        b_end = b["end"]
                        # Overlap: slot_start < b_end and slot_end > b_start
                        if slot_start < b_end and slot_end > b_start:
                            collides = True
                            break

                    if not collides:
                        time_label = slot_start.strftime("%I:%M %p")
                        available_slots.append({
                            "start": slot_start.isoformat(),
                            "end": slot_end.isoformat(),
                            "date": target_date.isoformat(),
                            "time_formatted": time_label,
                            "display": f"{time_label} ({target_date.strftime('%a, %b %d')})",
                        })

                curr += step

        return available_slots


class GoogleCalendarService(CalendarServiceInterface):
    """
    Google Calendar API provider using OAuth 2.0 and googleapiclient.
    """

    def __init__(
        self,
        calendar_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
    ):
        self.calendar_id = calendar_id or getattr(settings, "GOOGLE_CALENDAR_ID", "primary")
        self.client_id = client_id or getattr(settings, "GOOGLE_CLIENT_ID", "")
        self.client_secret = client_secret or getattr(settings, "GOOGLE_CLIENT_SECRET", "")
        self.refresh_token = refresh_token or getattr(settings, "GOOGLE_REFRESH_TOKEN", "")

    def _get_service(self, clinic: Clinic):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        # Check clinic config override if present
        config = getattr(clinic, "google_calendar_config", None)
        refresh_token = (config.refresh_token if config and config.refresh_token else self.refresh_token)
        cal_id = (config.calendar_id if config and config.calendar_id else self.calendar_id)

        if not refresh_token or not self.client_id or not self.client_secret:
            raise CalendarError("Missing Google Calendar credentials or refresh token.")

        creds = Credentials(
            None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        return build("calendar", "v3", credentials=creds, cache_discovery=False), cal_id

    def get_available_slots(
        self,
        clinic: Clinic,
        target_date: date,
        duration_minutes: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
        busy_intervals = []

        try:
            service, cal_id = self._get_service(clinic)
            start_dt = datetime.combine(target_date, time.min, tzinfo=tz).isoformat()
            end_dt = datetime.combine(target_date, time.max, tzinfo=tz).isoformat()

            body = {
                "timeMin": start_dt,
                "timeMax": end_dt,
                "timeZone": clinic.timezone or "Asia/Kolkata",
                "items": [{"id": cal_id}],
            }
            res = service.freebusy().query(body=body).execute()
            calendars_data = res.get("calendars", {}).get(cal_id, {})
            busy_list = calendars_data.get("busy", [])

            for item in busy_list:
                busy_intervals.append({
                    "start": datetime.fromisoformat(item["start"]).astimezone(tz),
                    "end": datetime.fromisoformat(item["end"]).astimezone(tz),
                })
        except Exception as exc:
            logger.warning(f"Google Calendar freebusy query failed: {exc}. Proceeding with database schedule only.")

        return BaseAvailabilityCalculator.calculate_slots(
            clinic=clinic,
            target_date=target_date,
            busy_intervals=busy_intervals,
            duration_minutes=duration_minutes,
        )

    def create_event(
        self,
        clinic: Clinic,
        summary: str,
        start_at: datetime,
        end_at: datetime,
        description: str = "",
        idempotency_key: Optional[str] = None,
        attendee_email: Optional[str] = None,
        attendee_name: Optional[str] = None,
    ) -> str:
        try:
            service, cal_id = self._get_service(clinic)
            tz_str = clinic.timezone or "Asia/Kolkata"
            body = {
                "summary": summary,
                "description": description,
                "start": {
                    "dateTime": start_at.isoformat(),
                    "timeZone": tz_str,
                },
                "end": {
                    "dateTime": end_at.isoformat(),
                    "timeZone": tz_str,
                },
            }
            if attendee_email:
                attendee = {"email": attendee_email}
                if attendee_name:
                    attendee["displayName"] = attendee_name
                body["attendees"] = [attendee]

            if idempotency_key:
                body["extendedProperties"] = {
                    "private": {"idempotency_key": idempotency_key}
                }

            event = service.events().insert(
                calendarId=cal_id,
                body=body,
                sendUpdates="all",
            ).execute()
            return event["id"]
        except Exception as exc:
            logger.error(f"Failed to create Google Calendar event: {exc}")
            raise CalendarError(f"Google Calendar event creation failed: {exc}") from exc

    def update_event(
        self,
        clinic: Clinic,
        event_id: str,
        start_at: datetime,
        end_at: datetime,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        attendee_email: Optional[str] = None,
        attendee_name: Optional[str] = None,
    ) -> bool:
        try:
            service, cal_id = self._get_service(clinic)
            tz_str = clinic.timezone or "Asia/Kolkata"
            patch_body = {
                "start": {"dateTime": start_at.isoformat(), "timeZone": tz_str},
                "end": {"dateTime": end_at.isoformat(), "timeZone": tz_str},
            }
            if summary:
                patch_body["summary"] = summary
            if description:
                patch_body["description"] = description
            if attendee_email:
                attendee = {"email": attendee_email}
                if attendee_name:
                    attendee["displayName"] = attendee_name
                patch_body["attendees"] = [attendee]

            service.events().patch(
                calendarId=cal_id,
                eventId=event_id,
                body=patch_body,
                sendUpdates="all",
            ).execute()
            return True
        except Exception as exc:
            logger.error(f"Failed to update Google Calendar event {event_id}: {exc}")
            raise CalendarError(f"Google Calendar update failed: {exc}") from exc

    def delete_event(
        self,
        clinic: Clinic,
        event_id: str,
    ) -> bool:
        try:
            service, cal_id = self._get_service(clinic)
            service.events().delete(
                calendarId=cal_id,
                eventId=event_id,
                sendUpdates="all",
            ).execute()
            return True
        except Exception as exc:
            logger.warning(f"Failed to delete Google Calendar event {event_id}: {exc}")
            return False


class MockCalendarService(CalendarServiceInterface):
    """
    Mock calendar service that calculates real slots using clinic hours and DB constraints,
    simulating external calendar events in memory.
    """

    def __init__(self):
        self.events = {}

    def get_available_slots(
        self,
        clinic: Clinic,
        target_date: date,
        duration_minutes: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
        busy_intervals = []

        # Include simulated calendar events
        for eid, ev in self.events.items():
            if ev.get("clinic_id") == str(clinic.id):
                ev_start = ev["start"].astimezone(tz)
                ev_end = ev["end"].astimezone(tz)
                if ev_start.date() == target_date or ev_end.date() == target_date:
                    busy_intervals.append({"start": ev_start, "end": ev_end})

        return BaseAvailabilityCalculator.calculate_slots(
            clinic=clinic,
            target_date=target_date,
            busy_intervals=busy_intervals,
            duration_minutes=duration_minutes,
        )

    def create_event(
        self,
        clinic: Clinic,
        summary: str,
        start_at: datetime,
        end_at: datetime,
        description: str = "",
        idempotency_key: Optional[str] = None,
        attendee_email: Optional[str] = None,
        attendee_name: Optional[str] = None,
    ) -> str:
        # Check idempotency
        if idempotency_key:
            for eid, ev in self.events.items():
                if ev.get("idempotency_key") == idempotency_key:
                    return eid

        import uuid
        event_id = f"mock_cal_{uuid.uuid4().hex[:12]}"
        self.events[event_id] = {
            "clinic_id": str(clinic.id),
            "summary": summary,
            "description": description,
            "start": start_at,
            "end": end_at,
            "idempotency_key": idempotency_key,
            "attendee_email": attendee_email,
            "attendee_name": attendee_name,
        }
        return event_id

    def update_event(
        self,
        clinic: Clinic,
        event_id: str,
        start_at: datetime,
        end_at: datetime,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        attendee_email: Optional[str] = None,
        attendee_name: Optional[str] = None,
    ) -> bool:
        if event_id in self.events:
            self.events[event_id]["start"] = start_at
            self.events[event_id]["end"] = end_at
            if summary:
                self.events[event_id]["summary"] = summary
            if description:
                self.events[event_id]["description"] = description
            if attendee_email:
                self.events[event_id]["attendee_email"] = attendee_email
            if attendee_name:
                self.events[event_id]["attendee_name"] = attendee_name
            return True
        return False

    def delete_event(
        self,
        clinic: Clinic,
        event_id: str,
    ) -> bool:
        if event_id in self.events:
            del self.events[event_id]
            return True
        return False


def get_calendar_service(use_mock: bool = False) -> CalendarServiceInterface:
    refresh_token = getattr(settings, "GOOGLE_REFRESH_TOKEN", "")
    if use_mock or not refresh_token or refresh_token.startswith("mock_"):
        return MockCalendarService()
    return GoogleCalendarService()
