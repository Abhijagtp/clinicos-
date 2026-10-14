import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.clinics.models import Clinic, ClinicMembership
from apps.appointments.models import Appointment
from apps.patients.models import Patient
from apps.conversations.models import Conversation, ConversationState

logger = logging.getLogger(__name__)


class DashboardStatsView(APIView):
    """
    GET /api/v1/dashboard/stats/
    Provides comprehensive KPI metrics, today's schedule breakdown,
    next arriving patients, WhatsApp AI interaction counts, and calendar health.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user

        # 1. Resolve authorized clinic
        clinic = None
        membership = ClinicMembership.objects.filter(user=user, is_active=True).select_related("clinic").first()
        if membership:
            clinic = membership.clinic
        else:
            clinic = Clinic.objects.filter(status=Clinic.Status.ACTIVE).first()

        if not clinic:
            return Response({"error": "No active clinic found."}, status=status.HTTP_404_NOT_FOUND)

        # 2. Determine target date in clinic timezone
        tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
        now_local = datetime.now(tz)
        
        date_str = request.query_params.get("date")
        if date_str:
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                target_date = now_local.date()
        else:
            target_date = now_local.date()

        day_start = datetime.combine(target_date, time.min, tzinfo=tz)
        day_end = datetime.combine(target_date, time.max, tzinfo=tz)

        # 3. Appointments query for the target day
        day_appts = Appointment.objects.filter(
            clinic=clinic,
            starts_at__gte=day_start,
            starts_at__lte=day_end,
        ).select_related("patient")

        # Counts by status
        total_day_appts = day_appts.count()
        confirmed_count = day_appts.filter(
            status__in=[Appointment.Status.CONFIRMED, Appointment.Status.BOOKED]
        ).count()
        in_progress_count = day_appts.filter(
            status__in=[Appointment.Status.IN_CONSULTATION, Appointment.Status.CHECKED_IN]
        ).count()
        completed_count = day_appts.filter(status=Appointment.Status.COMPLETED).count()
        cancelled_count = day_appts.filter(status=Appointment.Status.CANCELLED).count()
        no_show_count = day_appts.filter(status=Appointment.Status.NO_SHOW).count()

        # 4. Next immediate upcoming patients today
        upcoming_qs = day_appts.filter(
            starts_at__gte=timezone.now(),
            status__in=[Appointment.Status.CONFIRMED, Appointment.Status.BOOKED, Appointment.Status.CHECKED_IN],
        ).order_by("starts_at")[:5]

        upcoming_list = []
        for appt in upcoming_qs:
            start_local = appt.starts_at.astimezone(tz)
            end_local = appt.ends_at.astimezone(tz)
            upcoming_list.append({
                "id": str(appt.id),
                "patient_name": appt.patient.display_name if appt.patient else "Valued Patient",
                "patient_phone": appt.patient.phone or appt.patient.whatsapp_id if appt.patient else "N/A",
                "patient_email": appt.patient.email if appt.patient else "",
                "starts_at": appt.starts_at.isoformat(),
                "ends_at": appt.ends_at.isoformat(),
                "time_formatted": start_local.strftime("%I:%M %p"),
                "time_range": f"{start_local.strftime('%I:%M %p')} - {end_local.strftime('%I:%M %p')}",
                "status": appt.status,
                "appointment_type": appt.appointment_type,
                "is_calendar_synced": bool(appt.external_calendar_event_id),
            })

        # 5. WhatsApp & Conversational AI Metrics
        conversations_qs = Conversation.objects.filter(clinic=clinic)
        chats_today = conversations_qs.filter(last_message_at__gte=day_start).count()
        needs_human = conversations_qs.filter(current_state=ConversationState.HUMAN_HANDOFF).count()
        booking_in_progress = conversations_qs.filter(
            current_state__in=[
                ConversationState.COLLECTING_DETAILS,
                ConversationState.OFFERING_SLOTS,
                ConversationState.AWAITING_CONFIRMATION,
            ]
        ).count()

        # 6. Patient Directory Metrics
        total_patients = Patient.objects.filter(clinic=clinic, is_active=True).count()
        week_ago = timezone.now() - timedelta(days=7)
        new_patients_week = Patient.objects.filter(clinic=clinic, created_at__gte=week_ago).count()

        # 7. Total all-time appointment counts
        all_appts_count = Appointment.objects.filter(clinic=clinic).count()

        data = {
            "clinic": {
                "id": str(clinic.id),
                "name": clinic.name,
                "timezone": clinic.timezone,
            },
            "date": target_date.isoformat(),
            "today": {
                "total": total_day_appts,
                "confirmed": confirmed_count,
                "in_progress": in_progress_count,
                "completed": completed_count,
                "cancelled": cancelled_count,
                "no_show": no_show_count,
            },
            "upcoming_today": upcoming_list,
            "conversations": {
                "chats_today": chats_today,
                "needs_human_escalation": needs_human,
                "booking_in_progress": booking_in_progress,
                "ai_assistant_status": "active",
            },
            "patients": {
                "total_registered": total_patients,
                "new_this_week": new_patients_week,
            },
            "all_time": {
                "total_appointments": all_appts_count,
            },
            "calendar": {
                "status": "connected",
                "sync_mode": "bi-directional",
                "attendee_invites_enabled": True,
            },
        }

        return Response(data, status=status.HTTP_200_OK)
