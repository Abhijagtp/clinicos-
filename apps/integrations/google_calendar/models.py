import uuid
from django.db import models
from apps.clinics.models import Clinic


class GoogleCalendarConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.OneToOneField(
        Clinic,
        on_delete=models.CASCADE,
        related_name="google_calendar_config",
    )
    calendar_id = models.CharField(max_length=255, default="primary")
    refresh_token = models.CharField(max_length=512, blank=True)
    access_token = models.CharField(max_length=512, blank=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    is_connected = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        status = "Connected" if self.is_connected else "Disconnected"
        return f"{self.clinic.name} Google Calendar ({status})"
