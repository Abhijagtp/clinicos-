import re
from rest_framework import serializers
from django.utils import timezone
from apps.patients.models import Patient
from apps.appointments.models import Appointment
from apps.consultations.models import Consultation


class PatientAppointmentSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Appointment
        fields = [
            "id",
            "starts_at",
            "ends_at",
            "status",
            "appointment_type",
            "reason",
            "internal_notes",
            "created_at",
        ]


class PatientSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    total_appointments = serializers.SerializerMethodField()
    last_visit_at = serializers.SerializerMethodField()
    upcoming_appointment_at = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = [
            "id",
            "clinic_id",
            "patient_number",
            "name",
            "first_name",
            "last_name",
            "display_name",
            "date_of_birth",
            "sex",
            "whatsapp_id",
            "phone",
            "email",
            "consent_status",
            "communication_preferences",
            "notes",
            "is_active",
            "total_appointments",
            "last_visit_at",
            "upcoming_appointment_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "clinic_id",
            "patient_number",
            "display_name",
            "total_appointments",
            "last_visit_at",
            "upcoming_appointment_at",
            "created_at",
            "updated_at",
        ]

    def get_total_appointments(self, obj):
        return obj.appointments.count()

    def get_last_visit_at(self, obj):
        past = (
            obj.appointments.filter(
                status__in=[Appointment.Status.CONFIRMED, Appointment.Status.COMPLETED],
                starts_at__lt=timezone.now(),
            )
            .order_by("-starts_at")
            .values_list("starts_at", flat=True)
            .first()
        )
        return past.isoformat() if past else None

    def get_upcoming_appointment_at(self, obj):
        upcoming = (
            obj.appointments.filter(
                status=Appointment.Status.CONFIRMED,
                starts_at__gte=timezone.now(),
            )
            .order_by("starts_at")
            .values_list("starts_at", flat=True)
            .first()
        )
        return upcoming.isoformat() if upcoming else None


class PatientCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = [
            "name",
            "first_name",
            "last_name",
            "date_of_birth",
            "sex",
            "whatsapp_id",
            "phone",
            "email",
            "consent_status",
            "communication_preferences",
            "notes",
            "is_active",
        ]

    def validate_phone(self, value):
        if not value:
            return ""
        # Strip characters like spaces, dashes, parentheses
        cleaned = re.sub(r"[\s\-\(\)\+]", "", value)
        if cleaned and not cleaned.isdigit():
            raise serializers.ValidationError("Phone number must contain only numeric digits.")
        return cleaned

    def validate_whatsapp_id(self, value):
        if not value:
            return ""
        cleaned = re.sub(r"[\s\-\(\)\+]", "", value)
        if cleaned and not cleaned.isdigit():
            raise serializers.ValidationError("WhatsApp ID must contain only numeric digits.")
        return cleaned

    def validate(self, attrs):
        # Ensure at least a name or contact identifier is provided
        name = attrs.get("name") or attrs.get("first_name") or attrs.get("last_name")
        phone = attrs.get("phone")
        whatsapp_id = attrs.get("whatsapp_id")

        if not self.instance and not (name or phone or whatsapp_id):
            raise serializers.ValidationError(
                "A patient must have at least a name, phone number, or WhatsApp ID."
            )
        return attrs


class PatientDetailSerializer(PatientSerializer):
    recent_appointments = serializers.SerializerMethodField()
    recent_consultations = serializers.SerializerMethodField()

    class Meta(PatientSerializer.Meta):
        fields = PatientSerializer.Meta.fields + [
            "recent_appointments",
            "recent_consultations",
        ]

    def get_recent_appointments(self, obj):
        appointments = obj.appointments.order_by("-starts_at")[:10]
        return PatientAppointmentSummarySerializer(appointments, many=True).data

    def get_recent_consultations(self, obj):
        consultations = obj.consultations.select_related("doctor").order_by("-started_at")[:10]
        return [
            {
                "id": str(c.id),
                "status": c.status,
                "started_at": c.started_at.isoformat() if c.started_at else None,
                "completed_at": c.completed_at.isoformat() if c.completed_at else None,
                "doctor_name": c.doctor.get_full_name() or c.doctor.email,
            }
            for c in consultations
        ]
