import logging
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.clinics.models import ClinicMembership
from apps.patients.models import Patient
from apps.appointments.models import Appointment
from .models import Consultation, ClinicalNote

logger = logging.getLogger(__name__)


class ClinicalNoteSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    reviewed_by_name = serializers.SerializerMethodField()
    finalized_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ClinicalNote
        fields = [
            "id",
            "consultation",
            "status",
            "source_transcript",
            "subjective",
            "objective",
            "assessment",
            "plan",
            "ai_generated",
            "created_by",
            "created_by_name",
            "reviewed_by",
            "reviewed_by_name",
            "reviewed_at",
            "finalized_by",
            "finalized_by_name",
            "finalized_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "consultation",
            "created_by",
            "created_by_name",
            "reviewed_by",
            "reviewed_by_name",
            "reviewed_at",
            "finalized_by",
            "finalized_by_name",
            "finalized_at",
            "created_at",
            "updated_at",
        ]

    def get_created_by_name(self, obj):
        if not obj.created_by:
            return None
        return obj.created_by.get_full_name() or obj.created_by.email

    def get_reviewed_by_name(self, obj):
        if not obj.reviewed_by:
            return None
        return obj.reviewed_by.get_full_name() or obj.reviewed_by.email

    def get_finalized_by_name(self, obj):
        if not obj.finalized_by:
            return None
        return obj.finalized_by.get_full_name() or obj.finalized_by.email


class ClinicalNoteCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClinicalNote
        fields = [
            "subjective",
            "objective",
            "assessment",
            "plan",
            "source_transcript",
            "ai_generated",
        ]


class ConsultationListSerializer(serializers.ModelSerializer):
    patient_name = serializers.SerializerMethodField()
    patient_number = serializers.SerializerMethodField()
    patient_phone = serializers.SerializerMethodField()
    doctor_name = serializers.SerializerMethodField()
    notes_count = serializers.SerializerMethodField()
    has_finalized_note = serializers.SerializerMethodField()
    appointment_type = serializers.SerializerMethodField()

    class Meta:
        model = Consultation
        fields = [
            "id",
            "clinic",
            "patient",
            "patient_name",
            "patient_number",
            "patient_phone",
            "doctor",
            "doctor_name",
            "appointment",
            "appointment_type",
            "status",
            "started_at",
            "completed_at",
            "notes_count",
            "has_finalized_note",
            "created_at",
            "updated_at",
        ]

    def get_patient_name(self, obj):
        return obj.patient.display_name if obj.patient else "Unknown Patient"

    def get_patient_number(self, obj):
        return obj.patient.patient_number if obj.patient else ""

    def get_patient_phone(self, obj):
        if not obj.patient:
            return ""
        return obj.patient.phone or obj.patient.whatsapp_id or ""

    def get_doctor_name(self, obj):
        if not obj.doctor:
            return "Unassigned"
        return f"Dr. {obj.doctor.get_full_name() or obj.doctor.email}"

    def get_notes_count(self, obj):
        return obj.notes.count()

    def get_has_finalized_note(self, obj):
        return obj.notes.filter(status=ClinicalNote.Status.FINALIZED).exists()

    def get_appointment_type(self, obj):
        if obj.appointment:
            return obj.appointment.appointment_type or "Appointment"
        return "Direct Walk-in"


class ConsultationDetailSerializer(serializers.ModelSerializer):
    patient = serializers.SerializerMethodField()
    doctor = serializers.SerializerMethodField()
    appointment = serializers.SerializerMethodField()
    notes = ClinicalNoteSerializer(many=True, read_only=True)

    class Meta:
        model = Consultation
        fields = [
            "id",
            "clinic",
            "patient",
            "doctor",
            "appointment",
            "status",
            "started_at",
            "completed_at",
            "notes",
            "created_at",
            "updated_at",
        ]

    def get_patient(self, obj):
        p = obj.patient
        if not p:
            return None
        return {
            "id": str(p.id),
            "name": p.display_name,
            "patient_number": p.patient_number,
            "phone": p.phone or p.whatsapp_id or "",
            "email": p.email or "",
            "sex": p.sex,
            "dob": p.date_of_birth.isoformat() if p.date_of_birth else None,
            "notes": p.notes or "",
            "is_active": p.is_active,
        }

    def get_doctor(self, obj):
        d = obj.doctor
        if not d:
            return None
        return {
            "id": d.id,
            "name": f"Dr. {d.get_full_name() or d.email}",
            "email": d.email,
        }

    def get_appointment(self, obj):
        a = obj.appointment
        if not a:
            return None
        return {
            "id": str(a.id),
            "starts_at": a.starts_at.isoformat(),
            "ends_at": a.ends_at.isoformat(),
            "status": a.status,
            "appointment_type": a.appointment_type,
            "reason": a.reason,
            "internal_notes": a.internal_notes,
        }


class ConsultationCreateSerializer(serializers.Serializer):
    patient_id = serializers.UUIDField(required=True)
    appointment_id = serializers.UUIDField(required=False, allow_null=True)
    doctor_id = serializers.IntegerField(required=False, allow_null=True)
    initial_notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        request = self.context.get("request")
        user = request.user if request else None

        # Verify patient exists
        patient = Patient.objects.filter(id=attrs["patient_id"]).first()
        if not patient:
            raise ValidationError({"patient_id": "Patient does not exist."})
        attrs["patient"] = patient

        # Verify appointment if provided
        appointment_id = attrs.get("appointment_id")
        if appointment_id:
            appointment = Appointment.objects.filter(id=appointment_id).first()
            if not appointment:
                raise ValidationError({"appointment_id": "Appointment does not exist."})
            if appointment.patient_id != patient.id:
                raise ValidationError({"appointment_id": "Appointment does not belong to this patient."})
            attrs["appointment"] = appointment
        else:
            attrs["appointment"] = None

        # Verify doctor
        doctor_id = attrs.get("doctor_id")
        if doctor_id:
            doctor = User.objects.filter(id=doctor_id).first()
            if not doctor:
                raise ValidationError({"doctor_id": "Selected doctor user not found."})
            attrs["doctor"] = doctor
        else:
            # Default to requesting user if doctor/owner
            attrs["doctor"] = user

        return attrs
