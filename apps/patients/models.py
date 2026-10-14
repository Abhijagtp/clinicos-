import uuid
from django.conf import settings
from django.db import models
from apps.clinics.models import Clinic


class Patient(models.Model):
    class Sex(models.TextChoices):
        FEMALE = "female", "Female"
        MALE = "male", "Male"
        OTHER = "other", "Other"
        NOT_RECORDED = "not_recorded", "Not recorded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name="patients")
    patient_number = models.CharField(max_length=32, blank=True)
    name = models.CharField(max_length=150, blank=True, help_text="Full name as supplied by patient")
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    sex = models.CharField(max_length=20, choices=Sex.choices, default=Sex.NOT_RECORDED)
    whatsapp_id = models.CharField(max_length=50, blank=True, db_index=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    consent_status = models.BooleanField(default=True, help_text="Consent for WhatsApp appointment communication")
    communication_preferences = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, help_text="Administrative notes only; do not store clinical notes here.")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_patients",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["clinic", "patient_number"], name="unique_patient_number_per_clinic"),
        ]
        indexes = [
            models.Index(fields=["clinic", "whatsapp_id"]),
            models.Index(fields=["clinic", "phone"]),
            models.Index(fields=["clinic", "name"]),
        ]

    def save(self, *args, **kwargs):
        if not self.patient_number:
            self.patient_number = f"PT-{uuid.uuid4().hex[:8].upper()}"
        if not self.name and (self.first_name or self.last_name):
            self.name = f"{self.first_name} {self.last_name}".strip()
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        return self.name or f"{self.first_name} {self.last_name}".strip() or "Valued Patient"

    def __str__(self):
        return f"{self.patient_number}: {self.display_name}".strip()
