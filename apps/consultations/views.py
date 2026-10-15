import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError

from apps.clinics.models import Clinic, ClinicMembership
from apps.patients.models import Patient
from apps.appointments.models import Appointment
from .models import Consultation, ClinicalNote
from .serializers import (
    ConsultationListSerializer,
    ConsultationDetailSerializer,
    ConsultationCreateSerializer,
    ClinicalNoteSerializer,
    ClinicalNoteCreateUpdateSerializer,
)
from .services import generate_soap_note_from_text

logger = logging.getLogger(__name__)


def get_authorized_clinic_ids(user):
    """Returns clinic IDs user has membership in, or all if superuser."""
    if user.is_superuser:
        return Clinic.objects.values_list("id", flat=True)
    return ClinicMembership.objects.filter(
        user=user,
        is_active=True,
    ).values_list("clinic_id", flat=True)


def is_doctor_or_owner(user, clinic_id=None):
    """Checks if the user has doctor or owner privileges."""
    if user.is_superuser:
        return True
    qs = ClinicMembership.objects.filter(
        user=user,
        is_active=True,
        role__in=[ClinicMembership.Role.DOCTOR, ClinicMembership.Role.OWNER],
    )
    if clinic_id:
        qs = qs.filter(clinic_id=clinic_id)
    return qs.exists()


class ConsultationListCreateView(APIView):
    """
    GET  /api/v1/consultations/ — List and filter consultations
    POST /api/v1/consultations/ — Start a new consultation for a patient / appointment
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        allowed_clinic_ids = get_authorized_clinic_ids(request.user)
        queryset = Consultation.objects.filter(clinic_id__in=allowed_clinic_ids).select_related(
            "clinic", "patient", "doctor", "appointment"
        ).prefetch_related("notes")

        # Clinic filter
        clinic_param = request.query_params.get("clinic_id")
        if clinic_param:
            if str(clinic_param) not in [str(cid) for cid in allowed_clinic_ids] and not request.user.is_superuser:
                raise PermissionDenied("You do not have access to this clinic's consultations.")
            queryset = queryset.filter(clinic_id=clinic_param)

        # Patient filter
        patient_param = request.query_params.get("patient_id")
        if patient_param:
            queryset = queryset.filter(patient_id=patient_param)

        # Doctor filter
        doctor_param = request.query_params.get("doctor_id")
        if doctor_param:
            queryset = queryset.filter(doctor_id=doctor_param)

        # Status filter
        status_param = request.query_params.get("status")
        if status_param and status_param in Consultation.Status.values:
            queryset = queryset.filter(status=status_param)

        # Date filter (by started_at)
        date_str = request.query_params.get("date")
        if date_str:
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                queryset = queryset.filter(started_at__date=target_date)
            except ValueError:
                pass

        # Search across patient name, phone, patient number, doctor name
        search_query = request.query_params.get("search") or request.query_params.get("q")
        if search_query:
            sq = search_query.strip()
            queryset = queryset.filter(
                Q(patient__name__icontains=sq)
                | Q(patient__first_name__icontains=sq)
                | Q(patient__last_name__icontains=sq)
                | Q(patient__patient_number__icontains=sq)
                | Q(patient__phone__icontains=sq)
                | Q(doctor__first_name__icontains=sq)
                | Q(doctor__last_name__icontains=sq)
                | Q(doctor__email__icontains=sq)
            )

        queryset = queryset.order_by("-started_at")
        serializer = ConsultationListSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        serializer = ConsultationCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        patient = validated["patient"]
        appointment = validated.get("appointment")
        doctor = validated["doctor"]
        initial_notes = validated.get("initial_notes", "")

        allowed_clinic_ids = get_authorized_clinic_ids(request.user)
        if patient.clinic_id not in allowed_clinic_ids and not request.user.is_superuser:
            raise PermissionDenied("You do not have permission to start a consultation for this patient.")

        clinic = patient.clinic

        with transaction.atomic():
            consultation = Consultation.objects.create(
                clinic=clinic,
                patient=patient,
                appointment=appointment,
                doctor=doctor,
                status=Consultation.Status.IN_PROGRESS,
                started_at=timezone.now(),
            )

            # If linked to an appointment, transition appointment status to IN_CONSULTATION
            if appointment and appointment.status != Appointment.Status.IN_CONSULTATION:
                appointment.status = Appointment.Status.IN_CONSULTATION
                appointment.save(update_fields=["status", "updated_at"])

            # Create initial blank or draft SOAP note
            if initial_notes:
                parsed_soap = generate_soap_note_from_text(
                    initial_notes,
                    patient_context={"name": patient.display_name, "sex": patient.sex},
                )
                ClinicalNote.objects.create(
                    clinic=clinic,
                    consultation=consultation,
                    status=ClinicalNote.Status.DRAFT,
                    subjective=parsed_soap["subjective"],
                    objective=parsed_soap["objective"],
                    assessment=parsed_soap["assessment"],
                    plan=parsed_soap["plan"],
                    source_transcript=initial_notes,
                    created_by=request.user,
                )
            else:
                ClinicalNote.objects.create(
                    clinic=clinic,
                    consultation=consultation,
                    status=ClinicalNote.Status.DRAFT,
                    subjective="",
                    objective="",
                    assessment="",
                    plan="",
                    created_by=request.user,
                )

        logger.info(
            "Started consultation %s for patient %s with doctor %s",
            consultation.id,
            patient.id,
            doctor.id,
        )

        detail_serializer = ConsultationDetailSerializer(consultation)
        return Response(detail_serializer.data, status=status.HTTP_201_CREATED)


class ConsultationDetailView(APIView):
    """
    GET   /api/v1/consultations/<uuid:id>/ — View consultation details & SOAP notes
    PATCH /api/v1/consultations/<uuid:id>/ — Update consultation status / complete visit
    """
    permission_classes = [IsAuthenticated]

    def _get_consultation(self, user, consultation_id):
        allowed_clinic_ids = get_authorized_clinic_ids(user)
        consultation = Consultation.objects.filter(id=consultation_id).select_related(
            "clinic", "patient", "doctor", "appointment"
        ).prefetch_related("notes").first()

        if not consultation:
            raise NotFound("Consultation not found.")
        if consultation.clinic_id not in allowed_clinic_ids and not user.is_superuser:
            raise PermissionDenied("You do not have access to this consultation.")
        return consultation

    def get(self, request, id, *args, **kwargs):
        consultation = self._get_consultation(request.user, id)
        serializer = ConsultationDetailSerializer(consultation)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, id, *args, **kwargs):
        consultation = self._get_consultation(request.user, id)
        new_status = request.data.get("status")

        if new_status and new_status in Consultation.Status.values:
            consultation.status = new_status
            if new_status == Consultation.Status.COMPLETED and not consultation.completed_at:
                consultation.completed_at = timezone.now()
                # Update linked appointment status to COMPLETED
                if consultation.appointment:
                    consultation.appointment.status = Appointment.Status.COMPLETED
                    consultation.appointment.save(update_fields=["status", "updated_at"])

            consultation.save(update_fields=["status", "completed_at", "updated_at"])

        serializer = ConsultationDetailSerializer(consultation)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ClinicalNoteCreateUpdateView(APIView):
    """
    POST  /api/v1/consultations/<uuid:id>/notes/ — Create or update SOAP clinical note
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, id, *args, **kwargs):
        allowed_clinic_ids = get_authorized_clinic_ids(request.user)
        consultation = Consultation.objects.filter(id=id).first()
        if not consultation:
            raise NotFound("Consultation not found.")
        if consultation.clinic_id not in allowed_clinic_ids and not request.user.is_superuser:
            raise PermissionDenied("Access denied.")

        note_id = request.data.get("note_id")
        note = None
        if note_id:
            note = ClinicalNote.objects.filter(id=note_id, consultation=consultation).first()
        else:
            # Check most recent note for this consultation
            note = ClinicalNote.objects.filter(consultation=consultation).order_by("-created_at").first()

        if note and note.status == ClinicalNote.Status.FINALIZED:
            raise ValidationError("Cannot modify a finalized clinical note. It is permanently locked.")

        serializer = ClinicalNoteCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if note:
            for attr, val in data.items():
                setattr(note, attr, val)
            note.save()
        else:
            note = ClinicalNote.objects.create(
                clinic=consultation.clinic,
                consultation=consultation,
                status=ClinicalNote.Status.DRAFT,
                created_by=request.user,
                **data,
            )

        return Response(ClinicalNoteSerializer(note).data, status=status.HTTP_200_OK)


class ClinicalNoteFinalizeView(APIView):
    """
    POST /api/v1/consultations/<uuid:id>/notes/<uuid:note_id>/finalize/
    Clinician sign-off: strictly requires doctor or owner role.
    Locks the note permanently against any further modifications.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, id, note_id, *args, **kwargs):
        allowed_clinic_ids = get_authorized_clinic_ids(request.user)
        consultation = Consultation.objects.filter(id=id).first()
        if not consultation:
            raise NotFound("Consultation not found.")
        if consultation.clinic_id not in allowed_clinic_ids and not request.user.is_superuser:
            raise PermissionDenied("Access denied.")

        # Strict Doctor verification:
        if not is_doctor_or_owner(request.user, consultation.clinic_id):
            return Response(
                {"detail": "Only registered doctors or clinic owners have authorization to sign off and finalize clinical SOAP notes."},
                status=status.HTTP_403_FORBIDDEN,
            )

        note = ClinicalNote.objects.filter(id=note_id, consultation=consultation).first()
        if not note:
            raise NotFound("Clinical note not found.")

        if note.status == ClinicalNote.Status.FINALIZED:
            return Response(
                {"detail": "This clinical note is already finalized and signed.", "note": ClinicalNoteSerializer(note).data},
                status=status.HTTP_200_OK,
            )

        note.status = ClinicalNote.Status.FINALIZED
        note.finalized_by = request.user
        note.finalized_at = timezone.now()
        note.reviewed_by = request.user
        note.reviewed_at = timezone.now()
        note.full_clean()
        note.save()

        logger.info(
            "Clinical note %s finalized and signed by doctor %s",
            note.id,
            request.user.id,
        )

        return Response(ClinicalNoteSerializer(note).data, status=status.HTTP_200_OK)


class AIGenerateSoapView(APIView):
    """
    POST /api/v1/consultations/<uuid:id>/generate-soap/
    Takes raw dictation transcript or doctor bullet points and converts
    into clean structured SOAP format (Subjective, Objective, Assessment, Plan).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, id, *args, **kwargs):
        allowed_clinic_ids = get_authorized_clinic_ids(request.user)
        consultation = Consultation.objects.filter(id=id).select_related("patient").first()
        if not consultation:
            raise NotFound("Consultation not found.")
        if consultation.clinic_id not in allowed_clinic_ids and not request.user.is_superuser:
            raise PermissionDenied("Access denied.")

        raw_text = request.data.get("transcript") or request.data.get("notes") or ""
        if not raw_text.strip():
            return Response(
                {"error": "Please provide dictation transcript or notes to structure into SOAP."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        patient_context = {
            "name": consultation.patient.display_name if consultation.patient else "",
            "sex": consultation.patient.sex if consultation.patient else "",
            "notes": getattr(consultation.patient, "notes", ""),
        }

        soap_result = generate_soap_note_from_text(raw_text, patient_context)
        return Response(soap_result, status=status.HTTP_200_OK)
