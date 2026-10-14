import logging
from django.db.models.signals import post_delete
from django.dispatch import receiver
from apps.appointments.models import Appointment
from apps.integrations.google_calendar.service import get_calendar_service

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=Appointment)
def on_appointment_deleted(sender, instance, **kwargs):
    """
    Guarantees DB and Google Calendar remain in 100% sync.
    Whenever an appointment is deleted from the DB (admin, shell, or API),
    its corresponding Google Calendar event is automatically removed.
    """
    event_id = instance.external_calendar_event_id
    clinic = instance.clinic
    if event_id and clinic:
        try:
            cal = get_calendar_service()
            logger.info(f"Syncing deletion to Google Calendar for event {event_id} (Clinic: {clinic.name})...")
            cal.delete_event(clinic, event_id)
        except Exception as exc:
            logger.warning(f"Could not delete calendar event {event_id} on appointment delete: {exc}")
