import uuid
from django.conf import settings
from django.db import models
from apps.clinics.models import Clinic
from apps.patients.models import Patient
from apps.appointments.models import Appointment


class Consultation(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name="consultations")
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="consultations")
    appointment = models.ForeignKey(Appointment, null=True, blank=True, on_delete=models.PROTECT, related_name="consultations")
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="consultations")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["clinic", "patient", "started_at"])]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.patient_id and self.clinic_id and self.patient.clinic_id != self.clinic_id:
            raise ValidationError({"patient": "Patient must belong to the same clinic."})
        if self.appointment_id and self.clinic_id and self.appointment.clinic_id != self.clinic_id:
            raise ValidationError({"appointment": "Appointment must belong to the same clinic."})


class ClinicalNote(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        REVIEWED = "reviewed", "Reviewed"
        FINALIZED = "finalized", "Finalized"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name="clinical_notes")
    consultation = models.ForeignKey(Consultation, on_delete=models.PROTECT, related_name="notes")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    source_transcript = models.TextField(blank=True, help_text="Sensitive clinical data. Keep retention limited and access restricted.")
    subjective = models.TextField(blank=True)
    objective = models.TextField(blank=True)
    assessment = models.TextField(blank=True)
    plan = models.TextField(blank=True)
    ai_generated = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="reviewed_clinical_notes")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="finalized_clinical_notes")
    finalized_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_clinical_notes")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["clinic", "status", "created_at"])]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.consultation_id and self.clinic_id and self.consultation.clinic_id != self.clinic_id:
            raise ValidationError({"consultation": "Consultation must belong to the same clinic."})
        if self.status == self.Status.FINALIZED and not self.finalized_by_id:
            raise ValidationError({"finalized_by": "A clinician must be recorded when finalizing a note."})
