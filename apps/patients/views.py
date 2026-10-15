import logging
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, NotFound

from apps.clinics.models import Clinic, ClinicMembership
from apps.patients.models import Patient
from apps.appointments.models import Appointment
from apps.consultations.models import Consultation
from apps.patients.serializers import (
    PatientSerializer,
    PatientCreateUpdateSerializer,
    PatientDetailSerializer,
)

logger = logging.getLogger(__name__)


def get_authorized_clinic_ids(user):
    """Returns clinic IDs the user has active membership in, or all if superuser."""
    if user.is_superuser:
        return Clinic.objects.values_list("id", flat=True)
    return ClinicMembership.objects.filter(
        user=user,
        is_active=True,
    ).values_list("clinic_id", flat=True)


def get_user_primary_clinic(user):
    """Returns the primary active clinic for the staff user."""
    membership = (
        ClinicMembership.objects.filter(user=user, is_active=True)
        .select_related("clinic")
        .first()
    )
    if membership:
        return membership.clinic
    if user.is_superuser:
        return Clinic.objects.filter(status=Clinic.Status.ACTIVE).first()
    return None


class PatientListCreateView(APIView):
    """
    GET  /api/v1/patients/  — List & search patients for the requesting staff's clinic(s)
    POST /api/v1/patients/  — Create a new patient profile
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        allowed_clinic_ids = get_authorized_clinic_ids(request.user)
        queryset = Patient.objects.filter(clinic_id__in=allowed_clinic_ids)

        # Clinic query parameter filtering
        clinic_param = request.query_params.get("clinic_id")
        if clinic_param:
            allowed_str_ids = [str(cid) for cid in allowed_clinic_ids]
            if str(clinic_param) not in allowed_str_ids and not request.user.is_superuser:
                raise PermissionDenied("You do not have access to this clinic's patients.")
            queryset = queryset.filter(clinic_id=clinic_param)

        # Search query across full name, phone, WhatsApp, patient ID, and email
        search_query = request.query_params.get("search") or request.query_params.get("q")
        if search_query:
            search_query = search_query.strip()
            queryset = queryset.filter(
                Q(name__icontains=search_query)
                | Q(first_name__icontains=search_query)
                | Q(last_name__icontains=search_query)
                | Q(phone__icontains=search_query)
                | Q(whatsapp_id__icontains=search_query)
                | Q(patient_number__icontains=search_query)
                | Q(email__icontains=search_query)
            )

        # Active / inactive filter
        is_active_param = request.query_params.get("is_active")
        if is_active_param is not None:
            if is_active_param.lower() in ["true", "1"]:
                queryset = queryset.filter(is_active=True)
            elif is_active_param.lower() in ["false", "0"]:
                queryset = queryset.filter(is_active=False)

        # Sex filter
        sex_param = request.query_params.get("sex")
        if sex_param:
            queryset = queryset.filter(sex=sex_param)

        # Ordering
        ordering = request.query_params.get("ordering", "-created_at")
        allowed_orderings = ["created_at", "-created_at", "name", "-name", "patient_number", "-patient_number"]
        if ordering in allowed_orderings:
            queryset = queryset.order_by(ordering)
        else:
            queryset = queryset.order_by("-created_at")

        # Limit / offset pagination
        try:
            limit = int(request.query_params.get("limit", 50))
            offset = int(request.query_params.get("offset", 0))
            limit = min(max(1, limit), 200)
            offset = max(0, offset)
        except ValueError:
            limit = 50
            offset = 0

        total_count = queryset.count()
        paginated_queryset = queryset.prefetch_related("appointments")[offset : offset + limit]

        serializer = PatientSerializer(paginated_queryset, many=True)
        return Response({
            "count": total_count,
            "limit": limit,
            "offset": offset,
            "results": serializer.data,
        }, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        allowed_clinic_ids = get_authorized_clinic_ids(request.user)
        clinic_id = request.data.get("clinic_id")

        if clinic_id:
            allowed_str_ids = [str(cid) for cid in allowed_clinic_ids]
            if str(clinic_id) not in allowed_str_ids and not request.user.is_superuser:
                raise PermissionDenied("You do not have permission to register patients for this clinic.")
            clinic = get_object_or_404(Clinic, id=clinic_id)
        else:
            clinic = get_user_primary_clinic(request.user)
            if not clinic:
                return Response(
                    {"error": "No active clinic associated with your account."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        serializer = PatientCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        patient = serializer.save(
            clinic=clinic,
            created_by=request.user,
        )

        logger.info(
            "Created patient id=%s patient_number=%s in clinic=%s by user=%s",
            patient.id,
            patient.patient_number,
            clinic.id,
            request.user.id,
        )

        return Response(PatientSerializer(patient).data, status=status.HTTP_201_CREATED)


class PatientDetailView(APIView):
    """
    GET    /api/v1/patients/<uuid:id>/ — Retrieve patient profile and recent visit history
    PATCH  /api/v1/patients/<uuid:id>/ — Update patient profile details
    DELETE /api/v1/patients/<uuid:id>/ — Soft-deactivate patient profile
    """
    permission_classes = [IsAuthenticated]

    def _get_patient(self, user, patient_id):
        allowed_clinic_ids = get_authorized_clinic_ids(user)
        patient = Patient.objects.filter(id=patient_id).first()
        if not patient:
            raise NotFound("Patient not found.")
        if patient.clinic_id not in allowed_clinic_ids and not user.is_superuser:
            raise PermissionDenied("You do not have permission to view or manage this patient.")
        return patient

    def get(self, request, id, *args, **kwargs):
        patient = self._get_patient(request.user, id)
        serializer = PatientDetailSerializer(patient)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, id, *args, **kwargs):
        patient = self._get_patient(request.user, id)
        serializer = PatientCreateUpdateSerializer(patient, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated_patient = serializer.save()

        logger.info("Updated patient id=%s by user=%s", updated_patient.id, request.user.id)
        return Response(PatientDetailSerializer(updated_patient).data, status=status.HTTP_200_OK)

    def delete(self, request, id, *args, **kwargs):
        patient = self._get_patient(request.user, id)
        # Soft-delete patient record to protect historical medical charts
        patient.is_active = False
        patient.save(update_fields=["is_active", "updated_at"])

        logger.info("Deactivated patient id=%s by user=%s", patient.id, request.user.id)
        return Response(
            {"detail": "Patient deactivated successfully.", "is_active": False},
            status=status.HTTP_200_OK,
        )


class PatientTimelineView(APIView):
    """
    GET /api/v1/patients/<uuid:id>/timeline/
    Returns chronological timeline of interactions (appointments, consultations, notes)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, id, *args, **kwargs):
        allowed_clinic_ids = get_authorized_clinic_ids(request.user)
        patient = Patient.objects.filter(id=id).first()
        if not patient:
            raise NotFound("Patient not found.")
        if patient.clinic_id not in allowed_clinic_ids and not request.user.is_superuser:
            raise PermissionDenied("You do not have access to this patient's medical timeline.")

        timeline = []

        # Appointments
        appointments = Appointment.objects.filter(patient=patient).order_by("-starts_at")
        for appt in appointments:
            timeline.append({
                "type": "appointment",
                "timestamp": appt.starts_at.isoformat(),
                "id": str(appt.id),
                "title": f"Appointment: {appt.appointment_type or 'General Checkup'}",
                "status": appt.status,
                "notes": appt.reason or appt.internal_notes,
                "ends_at": appt.ends_at.isoformat(),
            })

        # Consultations
        consultations = (
            Consultation.objects.filter(patient=patient)
            .select_related("doctor")
            .prefetch_related("notes")
            .order_by("-started_at")
        )
        for consult in consultations:
            notes_count = consult.notes.count()
            timeline.append({
                "type": "consultation",
                "timestamp": consult.started_at.isoformat(),
                "id": str(consult.id),
                "title": f"Consultation with Dr. {consult.doctor.get_full_name() or consult.doctor.email}",
                "status": consult.status,
                "notes_count": notes_count,
                "completed_at": consult.completed_at.isoformat() if consult.completed_at else None,
            })

        # Sort combined timeline descending by timestamp
        timeline.sort(key=lambda item: item["timestamp"], reverse=True)

        return Response({
            "patient_id": str(patient.id),
            "patient_name": patient.display_name,
            "patient_number": patient.patient_number,
            "timeline": timeline,
        }, status=status.HTTP_200_OK)
