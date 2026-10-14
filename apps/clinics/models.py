import uuid
from django.conf import settings
from django.db import models


def default_clinic_hours():
    # 0=Monday through 5=Saturday: 09:00 - 13:00, 14:00 - 19:00. Sunday closed.
    weekday_slots = [
        {"start": "09:00", "end": "13:00"},
        {"start": "14:00", "end": "19:00"},
    ]
    return {
        "0": weekday_slots,
        "1": weekday_slots,
        "2": weekday_slots,
        "3": weekday_slots,
        "4": weekday_slots,
        "5": weekday_slots,
        "6": [],  # Sunday closed by default
    }


class Clinic(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=180)
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    phone_number = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    default_appointment_duration = models.PositiveIntegerField(
        default=30,
        help_text="Default appointment duration in minutes",
    )
    clinic_hours = models.JSONField(
        default=default_clinic_hours,
        blank=True,
        help_text="Configurable opening hours by weekday (0=Mon to 6=Sun)",
    )
    slot_buffer_minutes = models.PositiveIntegerField(
        default=0,
        help_text="Buffer time between appointments in minutes",
    )
    min_notice_hours = models.PositiveIntegerField(
        default=1,
        help_text="Minimum advance notice required to book an appointment (hours)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    def __str__(self):
        return self.name


class ClinicMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        DOCTOR = "doctor", "Doctor"
        RECEPTIONIST = "receptionist", "Receptionist"
        ASSISTANT = "assistant", "Assistant"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="clinic_memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["clinic", "user"], name="unique_clinic_user_membership")]
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self):
        return f"{self.user} — {self.clinic} ({self.role})"
