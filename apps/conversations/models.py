import uuid
from django.db import models
from django.utils import timezone
from apps.clinics.models import Clinic
from apps.patients.models import Patient


class ConversationState(models.TextChoices):
    IDLE = "IDLE", "Idle"
    IDENTIFYING_INTENT = "IDENTIFYING_INTENT", "Identifying Intent"
    COLLECTING_DETAILS = "COLLECTING_DETAILS", "Collecting Details"
    CHECKING_AVAILABILITY = "CHECKING_AVAILABILITY", "Checking Availability"
    OFFERING_SLOTS = "OFFERING_SLOTS", "Offering Slots"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION", "Awaiting Confirmation"
    BOOKING_IN_PROGRESS = "BOOKING_IN_PROGRESS", "Booking In Progress"
    BOOKED = "BOOKED", "Booked"
    RESCHEDULING = "RESCHEDULING", "Rescheduling"
    CANCELLING = "CANCELLING", "Cancelling"
    HUMAN_HANDOFF = "HUMAN_HANDOFF", "Human Handoff"


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name="conversations")
    patient = models.ForeignKey(
        Patient,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conversations",
    )
    whatsapp_sender_id = models.CharField(max_length=64, db_index=True)
    current_state = models.CharField(
        max_length=32,
        choices=ConversationState.choices,
        default=ConversationState.IDLE,
    )
    context = models.JSONField(
        default=dict,
        blank=True,
        help_text="Minimal conversation state context (e.g. offered slots, selected slot, booking intent).",
    )
    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_message_at", "-updated_at"]
        indexes = [
            models.Index(fields=["clinic", "whatsapp_sender_id"]),
            models.Index(fields=["current_state"]),
        ]

    def __str__(self):
        return f"Conv [{self.whatsapp_sender_id}] - {self.current_state}"


class MessageDirection(models.TextChoices):
    INBOUND = "inbound", "Inbound"
    OUTBOUND = "outbound", "Outbound"


class ProcessingStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PROCESSED = "processed", "Processed"
    DUPLICATE = "duplicate", "Duplicate"
    FAILED = "failed", "Failed"


class MessageLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    external_message_id = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        help_text="Idempotency key from WhatsApp message ID",
    )
    direction = models.CharField(max_length=16, choices=MessageDirection.choices)
    message_type = models.CharField(max_length=32, default="text")
    content = models.TextField(blank=True, help_text="Redacted or sanitized message summary")
    processing_status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.RECEIVED,
    )
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["timestamp"]
        indexes = [
            models.Index(fields=["conversation", "timestamp"]),
            models.Index(fields=["direction", "processing_status"]),
        ]

    def __str__(self):
        return f"{self.direction} msg {self.external_message_id} ({self.processing_status})"
