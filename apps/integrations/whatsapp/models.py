import uuid
from django.db import models
from apps.clinics.models import Clinic


class WhatsAppConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.OneToOneField(
        Clinic,
        on_delete=models.CASCADE,
        related_name="whatsapp_config",
    )
    phone_number_id = models.CharField(max_length=64, blank=True)
    access_token = models.CharField(max_length=512, blank=True)
    verify_token = models.CharField(max_length=128, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.clinic.name} WhatsApp ({self.phone_number_id or 'default'})"
