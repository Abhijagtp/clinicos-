import uuid
import logging
from datetime import datetime, timedelta, date
from typing import Dict, Any, Optional, Tuple, List
from django.db import transaction
from django.utils import timezone
from apps.clinics.models import Clinic
from apps.patients.models import Patient
from apps.appointments.models import Appointment, BookingReservation
from apps.audit.models import AuditEvent
from apps.integrations.google_calendar.service import (
    CalendarServiceInterface,
    CalendarError,
    get_calendar_service,
    parse_relative_date,
)

logger = logging.getLogger(__name__)


class BookingConflictError(Exception):
    """Raised when a slot is already booked or reserved."""
    pass


class BookingValidationError(Exception):
    """Raised when booking details are invalid."""
    pass


class BookingService:
    """
    Core domain booking service responsible for:
    - Slot verification & atomic reservation
    - Google Calendar synchronization
    - Appointment creation, rescheduling, and cancellation
    - Audit logging
    """

    def __init__(self, calendar_service: Optional[CalendarServiceInterface] = None):
        self.calendar_service = calendar_service or get_calendar_service()

    def get_or_create_patient(
        self,
        clinic: Clinic,
        whatsapp_id: str,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Patient:
        """
        Idempotently resolves or registers a patient for this clinic.
        """
        patient = Patient.objects.filter(clinic=clinic, whatsapp_id=whatsapp_id).first()
        if not patient and phone:
            patient = Patient.objects.filter(clinic=clinic, phone=phone).first()

        if not patient:
            patient = Patient.objects.create(
                clinic=clinic,
                whatsapp_id=whatsapp_id,
                phone=phone or whatsapp_id,
                name=name or "",
                email=email or "",
                consent_status=True,
            )
        else:
            fields_to_update = []
            if name and not patient.name:
                patient.name = name
                fields_to_update.append("name")
            if email and not patient.email:
                patient.email = email
                fields_to_update.append("email")
            if fields_to_update:
                patient.save(update_fields=fields_to_update)

        return patient

    def get_available_slots(
        self,
        clinic: Clinic,
        target_date: date,
        duration_minutes: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self.calendar_service.get_available_slots(
            clinic=clinic,
            target_date=target_date,
            duration_minutes=duration_minutes,
        )

    def reserve_slot(
        self,
        clinic: Clinic,
        patient: Optional[Patient],
        whatsapp_sender_id: str,
        start_at: datetime,
        end_at: datetime,
        ttl_minutes: int = 10,
        idempotency_key: Optional[str] = None,
    ) -> BookingReservation:
        """
        Creates a temporary lock/reservation in the database before external API calls.
        """
        idempotency_key = idempotency_key or f"res_{clinic.id}_{start_at.isoformat()}_{whatsapp_sender_id}"

        # Clean expired reservations
        BookingReservation.objects.filter(
            clinic=clinic,
            status=BookingReservation.Status.PENDING,
            expires_at__lt=timezone.now(),
        ).update(status=BookingReservation.Status.EXPIRED)

        with transaction.atomic():
            # Check existing conflicting appointment
            has_conflict = Appointment.objects.filter(
                clinic=clinic,
                starts_at__lt=end_at,
                ends_at__gt=start_at,
            ).exclude(status__in=[Appointment.Status.CANCELLED, Appointment.Status.NO_SHOW]).exists()

            if has_conflict:
                raise BookingConflictError("The requested appointment slot is already booked.")

            # Check existing active reservation for someone else
            existing_res = BookingReservation.objects.filter(
                clinic=clinic,
                starts_at__lt=end_at,
                ends_at__gt=start_at,
                status=BookingReservation.Status.PENDING,
                expires_at__gt=timezone.now(),
            ).exclude(idempotency_key=idempotency_key).exists()

            if existing_res:
                raise BookingConflictError("The requested slot is currently reserved by another patient.")

            reservation, _ = BookingReservation.objects.update_or_create(
                idempotency_key=idempotency_key,
                defaults={
                    "clinic": clinic,
                    "patient": patient,
                    "whatsapp_sender_id": whatsapp_sender_id,
                    "starts_at": start_at,
                    "ends_at": end_at,
                    "status": BookingReservation.Status.PENDING,
                    "expires_at": timezone.now() + timedelta(minutes=ttl_minutes),
                },
            )
            return reservation

    def confirm_booking(
        self,
        clinic: Clinic,
        patient: Patient,
        start_at: datetime,
        end_at: datetime,
        appointment_type: str = "dental_consultation",
        booking_source: str = "whatsapp",
        idempotency_key: Optional[str] = None,
        reason: str = "",
    ) -> Appointment:
        """
        Finalizes an appointment with safe calendar synchronization and compensation on failure.
        """
        id_key = idempotency_key or f"book_{clinic.id}_{start_at.isoformat()}_{patient.id}"

        # 1. Atomic reservation verification
        reservation = self.reserve_slot(
            clinic=clinic,
            patient=patient,
            whatsapp_sender_id=patient.whatsapp_id,
            start_at=start_at,
            end_at=end_at,
            ttl_minutes=5,
            idempotency_key=f"res_{id_key}",
        )

        cal_event_id = ""
        try:
            # 2. External Google Calendar event creation
            summary = f"Dental Appointment: {patient.display_name}"
            description = (
                f"Clinic: {clinic.name}\n"
                f"Patient: {patient.display_name}\n"
                f"Phone: {patient.phone or patient.whatsapp_id}\n"
                f"Email: {patient.email or 'N/A'}\n"
                f"Type: {appointment_type}\n"
                f"Source: {booking_source}"
            )
            cal_event_id = self.calendar_service.create_event(
                clinic=clinic,
                summary=summary,
                start_at=start_at,
                end_at=end_at,
                description=description,
                idempotency_key=id_key,
                attendee_email=patient.email,
                attendee_name=patient.display_name,
            )

            # 3. Persist confirmed appointment in Database
            with transaction.atomic():
                appointment = Appointment.objects.create(
                    clinic=clinic,
                    patient=patient,
                    starts_at=start_at,
                    ends_at=end_at,
                    status=Appointment.Status.CONFIRMED,
                    appointment_type=appointment_type,
                    booking_source=booking_source,
                    external_calendar_event_id=cal_event_id,
                    reason=reason,
                )
                reservation.status = BookingReservation.Status.CONFIRMED
                reservation.save(update_fields=["status"])

                AuditEvent.objects.create(
                    clinic=clinic,
                    action="appointment_created",
                    object_type="Appointment",
                    object_id=str(appointment.id),
                    metadata={
                        "patient_id": str(patient.id),
                        "starts_at": start_at.isoformat(),
                        "calendar_event_id": cal_event_id,
                        "source": booking_source,
                    },
                )
                return appointment

        except Exception as exc:
            logger.error(f"Error finalizing booking: {exc}. Performing safe compensation.")
            # Compensation: delete external calendar event if created
            if cal_event_id:
                try:
                    self.calendar_service.delete_event(clinic, cal_event_id)
                except Exception as comp_err:
                    logger.error(f"Failed to rollback calendar event {cal_event_id}: {comp_err}")
            # Release reservation
            reservation.status = BookingReservation.Status.RELEASED
            reservation.save(update_fields=["status"])
            raise

    def reschedule_appointment(
        self,
        clinic: Clinic,
        appointment: Appointment,
        new_start_at: datetime,
        new_end_at: datetime,
    ) -> Appointment:
        """
        Reschedules an appointment and syncs with external calendar.
        """
        if appointment.clinic_id != clinic.id:
            raise BookingValidationError("Appointment does not belong to this clinic.")

        with transaction.atomic():
            # Check slot conflict
            has_conflict = Appointment.objects.filter(
                clinic=clinic,
                starts_at__lt=new_end_at,
                ends_at__gt=new_start_at,
            ).exclude(id=appointment.id).exclude(
                status__in=[Appointment.Status.CANCELLED, Appointment.Status.NO_SHOW]
            ).exists()

            if has_conflict:
                raise BookingConflictError("The new slot is already booked.")

            old_start = appointment.starts_at
            summary = f"Dental Appointment: {appointment.patient.display_name}"
            description = (
                f"Clinic: {clinic.name}\n"
                f"Patient: {appointment.patient.display_name}\n"
                f"Phone: {appointment.patient.phone or appointment.patient.whatsapp_id}\n"
                f"Email: {appointment.patient.email or 'N/A'}\n"
                f"Type: {appointment.appointment_type}\n"
                f"Rescheduled to: {new_start_at.strftime('%Y-%m-%d %I:%M %p')}"
            )

            # Update calendar (moves event on clinic calendar AND patient personal calendar)
            if appointment.external_calendar_event_id:
                self.calendar_service.update_event(
                    clinic=clinic,
                    event_id=appointment.external_calendar_event_id,
                    start_at=new_start_at,
                    end_at=new_end_at,
                    summary=summary,
                    description=description,
                    attendee_email=appointment.patient.email,
                    attendee_name=appointment.patient.display_name,
                )
            else:
                # If appointment lacked a calendar event, create one now with attendee
                new_cal_id = self.calendar_service.create_event(
                    clinic=clinic,
                    summary=summary,
                    start_at=new_start_at,
                    end_at=new_end_at,
                    description=description,
                    attendee_email=appointment.patient.email,
                    attendee_name=appointment.patient.display_name,
                )
                appointment.external_calendar_event_id = new_cal_id

            appointment.starts_at = new_start_at
            appointment.ends_at = new_end_at
            appointment.status = Appointment.Status.CONFIRMED
            appointment.save(update_fields=["starts_at", "ends_at", "status", "external_calendar_event_id", "updated_at"])

            AuditEvent.objects.create(
                clinic=clinic,
                action="appointment_rescheduled",
                object_type="Appointment",
                object_id=str(appointment.id),
                metadata={
                    "old_starts_at": old_start.isoformat(),
                    "new_starts_at": new_start_at.isoformat(),
                },
            )
            return appointment

    def cancel_appointment(
        self,
        clinic: Clinic,
        appointment: Appointment,
        reason: str = "Patient requested cancellation",
    ) -> Appointment:
        """
        Cancels an appointment and removes from external calendar.
        """
        if appointment.clinic_id != clinic.id:
            raise BookingValidationError("Appointment does not belong to this clinic.")

        if appointment.external_calendar_event_id:
            self.calendar_service.delete_event(clinic, appointment.external_calendar_event_id)

        appointment.status = Appointment.Status.CANCELLED
        appointment.internal_notes = f"{appointment.internal_notes}\nCancelled: {reason}".strip()
        appointment.save(update_fields=["status", "internal_notes", "updated_at"])

        AuditEvent.objects.create(
            clinic=clinic,
            action="appointment_cancelled",
            object_type="Appointment",
            object_id=str(appointment.id),
            metadata={"reason": reason},
        )
        return appointment
