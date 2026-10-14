from rest_framework import serializers
from .models import Appointment, BookingReservation
from apps.patients.models import Patient
from apps.clinics.models import Clinic


class AppointmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    patient_phone = serializers.CharField(source="patient.phone", read_only=True)
    clinic_name = serializers.CharField(source="clinic.name", read_only=True)

    class Meta:
        model = Appointment
        fields = [
            "id",
            "clinic",
            "clinic_name",
            "patient",
            "patient_name",
            "patient_phone",
            "provider",
            "starts_at",
            "ends_at",
            "status",
            "appointment_type",
            "booking_source",
            "external_calendar_event_id",
            "reason",
            "internal_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "external_calendar_event_id", "created_at", "updated_at"]


class AppointmentCreateSerializer(serializers.Serializer):
    clinic_id = serializers.UUIDField()
    patient_id = serializers.UUIDField(required=False, allow_null=True)
    patient_name = serializers.CharField(required=False, allow_blank=True)
    patient_phone = serializers.CharField(required=False, allow_blank=True)
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    appointment_type = serializers.CharField(default="dental_consultation")
    reason = serializers.CharField(required=False, allow_blank=True)


class RescheduleSerializer(serializers.Serializer):
    new_starts_at = serializers.DateTimeField()
    new_ends_at = serializers.DateTimeField()


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, default="Staff requested cancellation")
