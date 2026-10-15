from rest_framework import serializers
from .models import Appointment, BookingReservation
from apps.patients.models import Patient
from apps.clinics.models import Clinic


from zoneinfo import ZoneInfo
from django.utils import timezone


class AppointmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    patient_phone = serializers.CharField(source="patient.phone", read_only=True)
    patient_email = serializers.CharField(source="patient.email", read_only=True)
    patient_number = serializers.CharField(source="patient.patient_number", read_only=True)
    clinic_name = serializers.CharField(source="clinic.name", read_only=True)
    time_formatted = serializers.SerializerMethodField()
    time_range = serializers.SerializerMethodField()
    date_formatted = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = [
            "id",
            "clinic",
            "clinic_name",
            "patient",
            "patient_name",
            "patient_phone",
            "patient_email",
            "patient_number",
            "provider",
            "starts_at",
            "ends_at",
            "time_formatted",
            "time_range",
            "date_formatted",
            "status",
            "appointment_type",
            "booking_source",
            "external_calendar_event_id",
            "reason",
            "internal_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "external_calendar_event_id",
            "time_formatted",
            "time_range",
            "date_formatted",
            "created_at",
            "updated_at",
        ]

    def _get_local_times(self, obj):
        tz_str = obj.clinic.timezone if obj.clinic and obj.clinic.timezone else "Asia/Kolkata"
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = ZoneInfo("Asia/Kolkata")
        start_local = obj.starts_at.astimezone(tz)
        end_local = obj.ends_at.astimezone(tz)
        return start_local, end_local

    def get_time_formatted(self, obj) -> str:
        start_local, _ = self._get_local_times(obj)
        return start_local.strftime("%I:%M %p").lstrip("0")

    def get_time_range(self, obj) -> str:
        start_local, end_local = self._get_local_times(obj)
        return f"{start_local.strftime('%I:%M %p').lstrip('0')} - {end_local.strftime('%I:%M %p').lstrip('0')}"

    def get_date_formatted(self, obj) -> str:
        start_local, _ = self._get_local_times(obj)
        return start_local.strftime("%a, %b %d")


class AppointmentStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Appointment.Status.choices, required=False)
    internal_notes = serializers.CharField(required=False, allow_blank=True)



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
