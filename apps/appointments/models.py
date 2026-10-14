import uuid
from django.conf import settings
from django.db import models
from apps.clinics.models import Clinic
from apps.patients.models import Patient


class Appointment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        BOOKED = "booked", "Booked"
        CHECKED_IN = "checked_in", "Checked in"
        IN_CONSULTATION = "in_consultation", "In consultation"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No show"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name="appointments")
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="appointments")
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="provider_appointments",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.CONFIRMED)
    appointment_type = models.CharField(max_length=80, default="dental_consultation")
    booking_source = models.CharField(max_length=32, default="whatsapp")
    external_calendar_event_id = models.CharField(max_length=255, blank=True, db_index=True)
    reason = models.CharField(max_length=240, blank=True)
    internal_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_appointments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at"]
        indexes = [
            models.Index(fields=["clinic", "starts_at", "status"]),
            models.Index(fields=["provider", "starts_at"]),
            models.Index(fields=["external_calendar_event_id"]),
        ]

    @property
    def start_at(self):
        return self.starts_at

    @property
    def end_at(self):
        return self.ends_at

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "Appointment end must be after start."})
        if self.patient_id and self.clinic_id and self.patient.clinic_id != self.clinic_id:
            raise ValidationError({"patient": "Patient must belong to the same clinic."})

    def __str__(self):
        return f"{self.patient} — {self.starts_at} ({self.status})"


class BookingReservation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        EXPIRED = "expired", "Expired"
        RELEASED = "released", "Released"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name="reservations")
    patient = models.ForeignKey(Patient, null=True, blank=True, on_delete=models.SET_NULL, related_name="reservations")
    whatsapp_sender_id = models.CharField(max_length=64, blank=True, db_index=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    idempotency_key = models.CharField(max_length=128, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["clinic", "starts_at", "ends_at", "status"]),
            models.Index(fields=["expires_at", "status"]),
        ]

    def __str__(self):
        return f"Reservation {self.idempotency_key} ({self.status}): {self.starts_at} - {self.ends_at}"
