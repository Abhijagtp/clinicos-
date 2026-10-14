import logging
from datetime import datetime, date
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, NotFound

from apps.clinics.models import Clinic, ClinicMembership
from apps.patients.models import Patient
from .models import Appointment
from .serializers import (
    AppointmentSerializer,
    AppointmentCreateSerializer,
    RescheduleSerializer,
    CancelSerializer,
)
from services.booking_service import (
    BookingService,
    BookingConflictError,
    BookingValidationError,
)
from apps.integrations.google_calendar.service import parse_relative_date

logger = logging.getLogger(__name__)


def get_authorized_clinic_ids(user):
    """Returns clinic IDs user has active membership in, or all if superuser."""
    if user.is_superuser:
        return Clinic.objects.values_list("id", flat=True)
    return ClinicMembership.objects.filter(
        user=user,
        is_active=True,
    ).values_list("clinic_id", flat=True)


class AppointmentListCreateView(APIView):
    """
    GET /api/v1/appointments/ — List appointments for authorized clinics.
    POST /api/v1/appointments/ — Create an appointment through validated domain service.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        allowed_clinic_ids = get_authorized_clinic_ids(request.user)
        clinic_id = request.query_params.get("clinic_id")

        queryset = Appointment.objects.filter(clinic_id__in=allowed_clinic_ids)
        if clinic_id:
            if not allowed_clinic_ids.filter(id=clinic_id).exists() if hasattr(allowed_clinic_ids, 'filter') else (clinic_id not in [str(c) for c in allowed_clinic_ids]):
                raise PermissionDenied("You do not have access to this clinic's appointments.")
            queryset = queryset.filter(clinic_id=clinic_id)

        status_param = request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)

        date_param = request.query_params.get("date")
        if date_param:
            try:
                target_date = datetime.strptime(date_param, "%Y-%m-%d").date()
                queryset = queryset.filter(starts_at__date=target_date)
            except ValueError:
                pass

        queryset = queryset.select_related("patient", "clinic").order_by("starts_at")
        serializer = AppointmentSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request, *args, **kwargs):
        serializer = AppointmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        clinic_id = data["clinic_id"]
        allowed_clinic_ids = [str(cid) for cid in get_authorized_clinic_ids(request.user)]
        if str(clinic_id) not in allowed_clinic_ids and not request.user.is_superuser:
            raise PermissionDenied("You do not have permission to book for this clinic.")

        clinic = get_object_or_404(Clinic, id=clinic_id)
        booking_service = BookingService()

        # Resolve or create patient
        patient = None
        if data.get("patient_id"):
            patient = get_object_or_404(Patient, id=data["patient_id"], clinic=clinic)
        elif data.get("patient_phone") or data.get("patient_name"):
            patient = booking_service.get_or_create_patient(
                clinic=clinic,
                whatsapp_id=data.get("patient_phone", ""),
                name=data.get("patient_name"),
                phone=data.get("patient_phone"),
            )
        else:
            return Response({"error": "Either patient_id or patient details required."}, status=400)

        try:
            appointment = booking_service.confirm_booking(
                clinic=clinic,
                patient=patient,
                start_at=data["starts_at"],
                end_at=data["ends_at"],
                appointment_type=data.get("appointment_type", "dental_consultation"),
                booking_source="staff_admin",
                reason=data.get("reason", ""),
            )
            return Response(AppointmentSerializer(appointment).data, status=status.HTTP_201_CREATED)
        except BookingConflictError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        except Exception as exc:
            logger.error(f"Failed to create appointment: {exc}")
            return Response({"error": f"Failed to create appointment: {exc}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AvailabilityView(APIView):
    """
    GET /api/v1/appointments/availability/
    Retrieves real available slots from clinic schedule and calendar provider.
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        clinic_id = request.query_params.get("clinic_id")
        date_str = request.query_params.get("date", "tomorrow")
        duration = request.query_params.get("duration")

        if clinic_id:
            clinic = get_object_or_404(Clinic, id=clinic_id)
        else:
            clinic = Clinic.objects.filter(status=Clinic.Status.ACTIVE).first()

        if not clinic:
            return Response({"error": "Clinic not found."}, status=404)

        target_date = parse_relative_date(date_str, clinic.timezone)
        if not target_date:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD or relative like 'tomorrow'."}, status=400)

        duration_min = int(duration) if duration and duration.isdigit() else clinic.default_appointment_duration
        booking_service = BookingService()
        slots = booking_service.get_available_slots(clinic, target_date, duration_minutes=duration_min)

        return Response({
            "clinic_id": str(clinic.id),
            "date": target_date.isoformat(),
            "timezone": clinic.timezone,
            "duration_minutes": duration_min,
            "available_slots": slots,
        })


class AppointmentRescheduleView(APIView):
    """
    POST /api/v1/appointments/{id}/reschedule/
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, id, *args, **kwargs):
        appointment = get_object_or_404(Appointment, id=id)
        allowed_clinic_ids = [str(cid) for cid in get_authorized_clinic_ids(request.user)]
        if str(appointment.clinic_id) not in allowed_clinic_ids and not request.user.is_superuser:
            raise PermissionDenied("You do not have permission to modify this appointment.")

        serializer = RescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        booking_service = BookingService()
        try:
            updated = booking_service.reschedule_appointment(
                clinic=appointment.clinic,
                appointment=appointment,
                new_start_at=data["new_starts_at"],
                new_end_at=data["new_ends_at"],
            )
            return Response(AppointmentSerializer(updated).data)
        except BookingConflictError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        except Exception as exc:
            return Response({"error": f"Failed to reschedule: {exc}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AppointmentCancelView(APIView):
    """
    POST /api/v1/appointments/{id}/cancel/
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, id, *args, **kwargs):
        appointment = get_object_or_404(Appointment, id=id)
        allowed_clinic_ids = [str(cid) for cid in get_authorized_clinic_ids(request.user)]
        if str(appointment.clinic_id) not in allowed_clinic_ids and not request.user.is_superuser:
            raise PermissionDenied("You do not have permission to cancel this appointment.")

        serializer = CancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        booking_service = BookingService()
        cancelled = booking_service.cancel_appointment(
            clinic=appointment.clinic,
            appointment=appointment,
            reason=serializer.validated_data.get("reason", "Cancelled by clinic staff"),
        )
        return Response(AppointmentSerializer(cancelled).data)
