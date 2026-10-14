import re
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Tuple, Dict, Any, List
from zoneinfo import ZoneInfo
from django.utils import timezone
from apps.clinics.models import Clinic
from apps.patients.models import Patient
from apps.appointments.models import Appointment
from apps.conversations.models import Conversation, ConversationState, MessageLog, MessageDirection, ProcessingStatus
from apps.ai_assistant.service import get_ai_assistant, AIAssistantInterface
from apps.integrations.google_calendar.service import parse_relative_date
from services.booking_service import BookingService, BookingConflictError

logger = logging.getLogger(__name__)


class ConversationOrchestrator:
    """
    State machine coordinator for the patient booking journey.
    Guarantees that slots are strictly calendar-derived and explicit confirmation is required.
    """

    def __init__(
        self,
        ai_assistant: Optional[AIAssistantInterface] = None,
        booking_service: Optional[BookingService] = None,
    ):
        self.ai = ai_assistant or get_ai_assistant()
        self.booking_service = booking_service or BookingService()

    def process_incoming_message(
        self,
        clinic: Clinic,
        sender_id: str,
        message_text: str,
        external_message_id: str,
    ) -> str:
        """
        Main entry point for incoming WhatsApp text messages.
        Returns the outbound response text to send back to the patient.
        """
        # 1. Resolve or create Conversation record
        conversation, _ = Conversation.objects.get_or_create(
            clinic=clinic,
            whatsapp_sender_id=sender_id,
            defaults={"current_state": ConversationState.IDLE, "context": {}},
        )

        # 2. Log Inbound Message for idempotency
        message_log, created = MessageLog.objects.get_or_create(
            external_message_id=external_message_id,
            defaults={
                "conversation": conversation,
                "direction": MessageDirection.INBOUND,
                "content": message_text[:250],
                "processing_status": ProcessingStatus.RECEIVED,
            },
        )
        if not created and message_log.processing_status == ProcessingStatus.PROCESSED:
            logger.info(f"Duplicate message {external_message_id} ignored.")
            return ""

        # 3. Associate Patient if available
        if not conversation.patient:
            patient = Patient.objects.filter(clinic=clinic, whatsapp_id=sender_id).first()
            if patient:
                conversation.patient = patient
                conversation.save(update_fields=["patient"])

        # 4. Run State Machine Transition
        try:
            reply_text = self._handle_state_transition(conversation, message_text)
            message_log.processing_status = ProcessingStatus.PROCESSED
            message_log.save(update_fields=["processing_status"])

            conversation.last_message_at = timezone.now()
            conversation.save(update_fields=["last_message_at", "updated_at"])

            # Log outbound message
            outbound_msg_id = f"out_{external_message_id}_{uuid_suffix()}"
            MessageLog.objects.create(
                conversation=conversation,
                external_message_id=outbound_msg_id,
                direction=MessageDirection.OUTBOUND,
                content=reply_text[:250],
                processing_status=ProcessingStatus.PROCESSED,
            )

            return reply_text
        except Exception as exc:
            logger.error(f"Error processing conversation state: {exc}", exc_info=True)
            message_log.processing_status = ProcessingStatus.FAILED
            message_log.save(update_fields=["processing_status"])
            return "Thank you for reaching out to Clinic OS. We encountered a temporary technical glitch. A clinic receptionist will follow up with you shortly."

    def _handle_state_transition(self, conv: Conversation, message_text: str) -> str:
        clinic = conv.clinic
        context = conv.context or {}
        context["current_state"] = conv.current_state

        # AI Intent Extraction
        ai_result = self.ai.extract_booking_intent(message_text, context)
        intent = ai_result.intent

        # If patient name provided, persist to patient record and context
        if ai_result.patient_name:
            context["patient_name"] = ai_result.patient_name
            self.booking_service.get_or_create_patient(
                clinic=clinic,
                whatsapp_id=conv.whatsapp_sender_id,
                name=ai_result.patient_name,
            )

        # Global Handlers: Human escalation / Emergency
        if ai_result.needs_human_escalation or intent == "human_support":
            conv.current_state = ConversationState.HUMAN_HANDOFF
            conv.context = context
            conv.save(update_fields=["current_state", "context"])
            return ai_result.conversational_reply or (
                "I have forwarded your request to our clinic reception team. "
                "A staff member will assist you promptly. For urgent medical emergencies, please call emergency services."
            )

        # Global Handlers: Cancellation request from any state
        if intent == "cancel" and conv.current_state != ConversationState.CANCELLING:
            active_appt = self._get_active_appointment(clinic, conv.whatsapp_sender_id)
            if not active_appt:
                context["pending_buttons"] = [
                    {"id": "book_appt", "title": "Book Appointment"},
                    {"id": "check_avail", "title": "Available Times"},
                ]
                conv.context = context
                conv.save(update_fields=["context"])
                return "We could not find an active upcoming appointment for your number. Would you like to schedule a new appointment?"
            conv.current_state = ConversationState.CANCELLING
            context["cancel_appointment_id"] = str(active_appt.id)
            context["pending_buttons"] = [
                {"id": "cancel_yes", "title": "Yes, Cancel"},
                {"id": "cancel_no", "title": "Keep Appointment"},
            ]
            conv.context = context
            conv.save(update_fields=["current_state", "context"])
            tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
            start_local = active_appt.starts_at.astimezone(tz)
            time_str = start_local.strftime("%I:%M %p on %A, %B %d")
            return (
                f"Are you sure you want to cancel your appointment scheduled for *{time_str}*?\n\n"
                f"The slot will be released on the clinic calendar."
            )

        # Global Handlers: Reschedule request
        if intent == "reschedule" and conv.current_state != ConversationState.RESCHEDULING:
            active_appt = self._get_active_appointment(clinic, conv.whatsapp_sender_id)
            if not active_appt:
                context["pending_buttons"] = [
                    {"id": "book_appt", "title": "Book Appointment"},
                    {"id": "check_avail", "title": "Available Times"},
                ]
                conv.context = context
                conv.save(update_fields=["context"])
                return "We could not find an active appointment to reschedule. Would you like to book a new appointment?"
            conv.current_state = ConversationState.RESCHEDULING
            context["reschedule_appointment_id"] = str(active_appt.id)
            tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
            start_local = active_appt.starts_at.astimezone(tz)
            curr_time_str = start_local.strftime("%I:%M %p on %A, %B %d")
            context["pending_buttons"] = self._get_reschedule_buttons(clinic)
            conv.context = context
            conv.save(update_fields=["current_state", "context"])
            return (
                f"Your current appointment is on *{curr_time_str}*.\n\n"
                f"Let's get that rescheduled for you! What day would you like to move it to?"
            )

        # --- STATE: IDLE ---
        if conv.current_state == ConversationState.IDLE:
            clean_m = message_text.lower().strip()

            if intent == "availability_inquiry" or "available time" in clean_m or "check_avail" in clean_m:
                return self._handle_availability_inquiry(conv, ai_result, message_text)

            if intent == "greeting":
                context["pending_buttons"] = [
                    {"id": "check_avail", "title": "Available Times"},
                    {"id": "book_appt", "title": "Book Appointment"},
                    {"id": "reschedule_appt", "title": "Reschedule / Cancel"},
                ]
                conv.context = context
                conv.save(update_fields=["context"])
                if ai_result.conversational_reply:
                    return ai_result.conversational_reply
                return (
                    f"Hello! Welcome to *{clinic.name}*! 👋🦷\n\n"
                    f"I can help you check available times, schedule a visit, or manage your appointments.\n"
                    f"How can we assist you today?"
                )

            if intent == "clinic_faq":
                context["pending_buttons"] = [
                    {"id": "check_avail", "title": "Available Times"},
                    {"id": "book_appt", "title": "Book Appointment"},
                ]
                conv.context = context
                conv.save(update_fields=["context"])
                return ai_result.conversational_reply or f"Our clinic is open Mon-Sat from 9:00 AM to 7:00 PM. We provide general checkups, dental cleanings, and specialized procedures. How may we assist you?"

            if "book appointment" in clean_m or "book_appt" in clean_m:
                return self._advance_booking_flow(conv, ai_result, message_text)

            if "reschedule" in clean_m or "reschedule_appt" in clean_m:
                active_appt = self._get_active_appointment(clinic, conv.whatsapp_sender_id)
                if active_appt:
                    conv.current_state = ConversationState.RESCHEDULING
                    context["reschedule_appointment_id"] = str(active_appt.id)
                    tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
                    curr_time_str = active_appt.starts_at.astimezone(tz).strftime("%I:%M %p on %A, %B %d")
                    context["pending_buttons"] = self._get_reschedule_buttons(clinic)
                    conv.context = context
                    conv.save(update_fields=["current_state", "context"])
                    return f"Your current appointment is on *{curr_time_str}*.\n\nWhat day would you like to reschedule it to?"
                else:
                    context["pending_buttons"] = [
                        {"id": "book_appt", "title": "Book Appointment"},
                        {"id": "check_avail", "title": "Available Times"},
                    ]
                    conv.context = context
                    conv.save(update_fields=["context"])
                    return "We couldn't find an active upcoming appointment to reschedule. Would you like to book a new appointment?"

            if intent == "book":
                return self._advance_booking_flow(conv, ai_result, message_text)

            # Natural AI Chatbot response for general inquiries in IDLE
            if ai_result.conversational_reply:
                context["pending_buttons"] = [
                    {"id": "check_avail", "title": "Available Times"},
                    {"id": "book_appt", "title": "Book Appointment"},
                ]
                conv.context = context
                conv.save(update_fields=["context"])
                return ai_result.conversational_reply

            context["pending_buttons"] = [
                {"id": "check_avail", "title": "Available Times"},
                {"id": "book_appt", "title": "Book Appointment"},
                {"id": "reschedule_appt", "title": "Reschedule / Cancel"},
            ]
            conv.context = context
            conv.save(update_fields=["context"])
            return f"Welcome to *{clinic.name}*! You can check available times, book, reschedule, or cancel dental appointments here. How can we assist you today?"

        # --- STATE: COLLECTING_DETAILS ---
        if conv.current_state == ConversationState.COLLECTING_DETAILS:
            clean_m = message_text.lower().strip()
            if intent == "availability_inquiry" or "available time" in clean_m or "check_avail" in clean_m:
                return self._handle_availability_inquiry(conv, ai_result, message_text)
            return self._advance_booking_flow(conv, ai_result, message_text)

        # --- STATE: OFFERING_SLOTS ---
        if conv.current_state == ConversationState.OFFERING_SLOTS:
            # Check if user selected one of the offered slots
            selected_slot = self._match_slot_choice(message_text, context.get("offered_slots", []))
            if selected_slot:
                context["selected_slot"] = selected_slot
                conv.current_state = ConversationState.AWAITING_CONFIRMATION
                display_time = selected_slot.get("display", selected_slot.get("time_formatted"))

                # Check if patient name & email are known
                existing_patient = Patient.objects.filter(clinic=clinic, whatsapp_id=conv.whatsapp_sender_id).first()
                patient_name = context.get("patient_name") or (existing_patient.name if existing_patient else None)
                patient_email = context.get("patient_email") or (existing_patient.email if existing_patient else None)

                if patient_name and patient_email:
                    context["patient_name"] = patient_name
                    context["patient_email"] = patient_email
                    context["pending_buttons"] = [
                        {"id": "confirm_yes", "title": "Confirm Booking"},
                        {"id": "confirm_no", "title": "Cancel"},
                    ]
                    conv.context = context
                    conv.save(update_fields=["current_state", "context"])
                    return (
                        f"Great! Please review your appointment details:\n\n"
                        f"👤 *Patient Name:* {patient_name}\n"
                        f"📧 *Email:* {patient_email}\n"
                        f"📱 *Phone:* {conv.whatsapp_sender_id}\n"
                        f"📅 *Date & Time:* {display_time}\n"
                        f"🏥 *Clinic:* {clinic.name}\n\n"
                        f"Please confirm: Would you like to book this appointment?\n"
                        f"👉 Reply *YES* to confirm, or *NO* to cancel."
                    )
                elif not patient_name:
                    context["sub_step"] = "awaiting_name"
                    conv.context = context
                    conv.save(update_fields=["current_state", "context"])
                    return (
                        f"Great choice! I have reserved *{display_time}* for you.\n\n"
                        f"Please confirm: To complete your booking, what is your *Full Name*?"
                    )
                else:
                    context["sub_step"] = "awaiting_email"
                    conv.context = context
                    conv.save(update_fields=["current_state", "context"])
                    return (
                        f"Thank you, *{patient_name}*!\n\n"
                        f"Please confirm: Could you share your *Email Address* for the appointment confirmation?"
                    )
            else:
                # User may have requested a different date/time
                return self._advance_booking_flow(conv, ai_result, message_text)

        # --- STATE: AWAITING_CONFIRMATION ---
        if conv.current_state == ConversationState.AWAITING_CONFIRMATION:
            sub_step = context.get("sub_step")
            display_time = context.get("selected_slot", {}).get("display", context.get("selected_slot", {}).get("time_formatted", ""))

            # Handle cancellation if negative
            if is_negative(message_text):
                conv.current_state = ConversationState.IDLE
                conv.context = {}
                conv.save(update_fields=["current_state", "context"])
                return "Understood. The appointment was not booked. Let us know whenever you'd like to schedule again."

            # Sub-step A: Awaiting Name
            if sub_step == "awaiting_name" and not is_affirmative(message_text):
                name_input = message_text.strip()
                cleaned_name = re.sub(r"^(?:my name is|i am|name:?|this is)\s*", "", name_input, flags=re.IGNORECASE).strip().title()

                # Check if email was also typed (e.g. "Arun Kumar, arun@gmail.com")
                email_match = re.search(r"\b([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b", name_input)
                if email_match:
                    context["patient_email"] = email_match.group(1).lower()
                    cleaned_name = re.sub(re.escape(email_match.group(1)), "", cleaned_name).strip(", ")

                context["patient_name"] = cleaned_name
                if context.get("patient_email"):
                    context.pop("sub_step", None)
                    context["pending_buttons"] = [
                        {"id": "confirm_yes", "title": "Confirm Booking"},
                        {"id": "confirm_no", "title": "Cancel"},
                    ]
                    conv.context = context
                    conv.save(update_fields=["context"])
                    return (
                        f"Thank you, *{cleaned_name}*!\n\n"
                        f"Please review your booking details:\n"
                        f"👤 *Patient:* {cleaned_name}\n"
                        f"📧 *Email:* {context['patient_email']}\n"
                        f"📱 *Phone:* {conv.whatsapp_sender_id}\n"
                        f"📅 *Date & Time:* {display_time}\n"
                        f"🏥 *Clinic:* {clinic.name}\n\n"
                        f"Please confirm: Are you sure you want to book this appointment?\n"
                        f"👉 Reply *YES* to confirm, or *NO* to cancel."
                    )
                else:
                    context["sub_step"] = "awaiting_email"
                    conv.context = context
                    conv.save(update_fields=["context"])
                    return (
                        f"Thank you, *{cleaned_name}*! 👍\n\n"
                        f"Could you please provide your *Email Address* for the appointment confirmation and calendar invite?"
                    )

            # Sub-step B: Awaiting Email
            if sub_step == "awaiting_email" and not is_affirmative(message_text):
                email_match = re.search(r"\b([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b", message_text)
                if email_match:
                    context["patient_email"] = email_match.group(1).lower()
                elif "@" in message_text:
                    context["patient_email"] = message_text.strip().lower()

                context.pop("sub_step", None)
                p_name = context.get("patient_name", "Valued Patient")
                p_email = context.get("patient_email", "N/A")
                context["pending_buttons"] = [
                    {"id": "confirm_yes", "title": "Confirm Booking"},
                    {"id": "confirm_no", "title": "Cancel"},
                ]
                conv.context = context
                conv.save(update_fields=["context"])
                return (
                    f"Please review your appointment details:\n\n"
                    f"👤 *Patient:* {p_name}\n"
                    f"📧 *Email:* {p_email}\n"
                    f"📱 *Phone:* {conv.whatsapp_sender_id}\n"
                    f"📅 *Date & Time:* {display_time}\n"
                    f"🏥 *Clinic:* {clinic.name}\n\n"
                    f"Please confirm: Are you sure you want to confirm this booking?\n"
                    f"👉 Reply *YES* to confirm, or *NO* to cancel."
                )

            # Final Step: Explicit Confirmation
            if ai_result.is_explicit_confirmation or is_affirmative(message_text):
                selected_slot = context.get("selected_slot")
                if not selected_slot:
                    conv.current_state = ConversationState.IDLE
                    conv.save(update_fields=["current_state"])
                    return "No pending appointment was found. What date would you like to schedule?"

                # Execute booking
                conv.current_state = ConversationState.BOOKING_IN_PROGRESS
                conv.save(update_fields=["current_state"])

                try:
                    patient = self.booking_service.get_or_create_patient(
                        clinic=clinic,
                        whatsapp_id=conv.whatsapp_sender_id,
                        name=context.get("patient_name"),
                        email=context.get("patient_email"),
                    )
                    start_at = datetime.fromisoformat(selected_slot["start"])
                    end_at = datetime.fromisoformat(selected_slot["end"])

                    appt = self.booking_service.confirm_booking(
                        clinic=clinic,
                        patient=patient,
                        start_at=start_at,
                        end_at=end_at,
                        appointment_type=context.get("appointment_type", "dental_consultation"),
                    )

                    conv.current_state = ConversationState.BOOKED
                    conv.context = {}
                    conv.save(update_fields=["current_state", "context"])

                    display_str = selected_slot.get("display") or selected_slot.get("time_formatted")
                    name_line = f"👤 *Patient:* {patient.name}\n" if patient.name else ""
                    email_line = f"📧 *Email:* {patient.email}\n" if patient.email else ""
                    phone_line = f"📱 *Phone:* {patient.phone or conv.whatsapp_sender_id}\n"
                    return (
                        f"🎉 *Appointment Confirmed!*\n\n"
                        f"{name_line}"
                        f"{email_line}"
                        f"{phone_line}"
                        f"📅 *When:* {display_str}\n"
                        f"🏥 *Where:* {clinic.name}\n\n"
                        f"Your appointment is locked in on Google Calendar. If you ever need to reschedule or cancel, simply message us here!"
                    )

                except BookingConflictError:
                    # Slot claimed concurrently; re-offer remaining slots
                    conv.current_state = ConversationState.OFFERING_SLOTS
                    conv.save(update_fields=["current_state"])
                    pref_date = parse_relative_date(context.get("preferred_date", "tomorrow"), clinic.timezone)
                    fresh_slots = self.booking_service.get_available_slots(clinic, pref_date)
                    context["offered_slots"] = fresh_slots
                    context["pending_buttons"] = self._get_representative_buttons(fresh_slots)
                    conv.context = context
                    conv.save(update_fields=["context"])
                    if fresh_slots:
                        times = " and ".join([s["time_formatted"] for s in fresh_slots[:2]])
                        return f"Apologies, that slot was just booked by another patient. We have {times} available. Which would you prefer?"
                    return "Apologies, that slot was taken and no more slots are available today. Would you like to check tomorrow?"

            elif is_negative(message_text):
                conv.current_state = ConversationState.IDLE
                conv.context = {}
                conv.save(update_fields=["current_state", "context"])
                return "Understood. The appointment was not booked. Let us know whenever you'd like to schedule again."

            # Check if user selected or switched to a different slot
            alt_slot = self._match_slot_choice(message_text, context.get("offered_slots", []))
            if alt_slot:
                context["selected_slot"] = alt_slot
                new_display = alt_slot.get("display", alt_slot.get("time_formatted"))
                p_name = context.get("patient_name", "Valued Patient")
                p_email = context.get("patient_email", "N/A")
                context["pending_buttons"] = [
                    {"id": "confirm_yes", "title": "Confirm Booking"},
                    {"id": "confirm_no", "title": "Cancel"},
                ]
                conv.context = context
                conv.save(update_fields=["context"])
                return (
                    f"Updated! Please review your updated appointment details:\n\n"
                    f"👤 *Patient:* {p_name}\n"
                    f"📧 *Email:* {p_email}\n"
                    f"📱 *Phone:* {conv.whatsapp_sender_id}\n"
                    f"📅 *Date & Time:* {new_display}\n"
                    f"🏥 *Clinic:* {clinic.name}\n\n"
                    f"Please confirm: Would you like to confirm this booking?\n"
                    f"👉 Reply *YES* to confirm, or *NO* to cancel."
                )

            # If user asks for different date or availability (e.g. "tomorrow", "today", "different time", "available")
            clean_m = (message_text or "").lower()
            if any(w in clean_m for w in ["tomorrow", "today", "different", "change", "other", "available", "check"]) or intent in ["availability_inquiry", "book"]:
                conv.current_state = ConversationState.COLLECTING_DETAILS
                return self._advance_booking_flow(conv, ai_result, message_text)

            return "Please reply 'Yes' to confirm your appointment, or tell us if you'd prefer a different time."

        # --- STATE: CANCELLING ---
        if conv.current_state == ConversationState.CANCELLING:
            clean_m = (message_text or "").lower().strip()
            if ai_result.is_cancellation_confirmation or is_affirmative(message_text) or "cancel_yes" in clean_m or "yes, cancel" in clean_m:
                appt_id = context.get("cancel_appointment_id")
                time_str = ""
                if appt_id:
                    appt = Appointment.objects.filter(id=appt_id, clinic=clinic).first()
                    if appt:
                        tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
                        time_str = appt.starts_at.astimezone(tz).strftime("%I:%M %p on %A, %B %d")
                        self.booking_service.cancel_appointment(clinic, appt)
                conv.current_state = ConversationState.IDLE
                context = {
                    "pending_buttons": [
                        {"id": "book_appt", "title": "Book Appointment"},
                        {"id": "check_avail", "title": "Available Times"},
                    ]
                }
                conv.context = context
                conv.save(update_fields=["current_state", "context"])
                appt_detail = f" for *{time_str}*" if time_str else ""
                return (
                    f"✅ *Appointment Cancelled Successfully*\n\n"
                    f"Your appointment{appt_detail} has been removed from Google Calendar and the clinic schedule.\n\n"
                    f"Whenever you would like to book a new appointment, tap below or message us!"
                )
            elif is_negative(message_text) or "keep" in clean_m or "cancel_no" in clean_m:
                conv.current_state = ConversationState.IDLE
                conv.context = {}
                conv.save(update_fields=["current_state", "context"])
                return "Your appointment remains confirmed as scheduled. We look forward to seeing you!"

            context["pending_buttons"] = [
                {"id": "cancel_yes", "title": "Yes, Cancel"},
                {"id": "cancel_no", "title": "Keep Appointment"},
            ]
            conv.context = context
            conv.save(update_fields=["context"])
            return "Please confirm: Would you like to cancel your appointment? The slot will be released."

        # --- STATE: RESCHEDULING ---
        if conv.current_state == ConversationState.RESCHEDULING:
            return self._advance_reschedule_flow(conv, ai_result, message_text)

        # Default fallback
        conv.current_state = ConversationState.IDLE
        conv.save(update_fields=["current_state"])
        return "How can we help you today with your dental appointment?"

    def _handle_availability_inquiry(self, conv: Conversation, ai_result, message_text: str) -> str:
        clinic = conv.clinic
        context = conv.context or {}
        tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
        today = datetime.now(tz).date()

        pref_date_str = ai_result.preferred_date or context.get("preferred_date")
        clean_m = (message_text or "").lower()
        if "tomorrow" in clean_m or "check_tomorrow" in clean_m:
            pref_date_str = "tomorrow"
        elif "day after tomorrow" in clean_m or "check_next_day" in clean_m:
            pref_date_str = "day after tomorrow"
        elif "today" in clean_m or "check_today" in clean_m:
            pref_date_str = "today"

        if pref_date_str:
            target_date = parse_relative_date(pref_date_str, clinic.timezone) or today
        else:
            # Check if today has open slots remaining
            today_slots = self.booking_service.get_available_slots(clinic, today)
            if today_slots:
                target_date = today
                pref_date_str = "today"
            else:
                target_date = today + timedelta(days=1)
                pref_date_str = "tomorrow"

        context["preferred_date"] = pref_date_str
        slots = self.booking_service.get_available_slots(clinic, target_date)

        pref_time = ai_result.preferred_time_range or context.get("preferred_time_range")
        if pref_time:
            filtered = self._filter_slots_by_preference(slots, pref_time)
            display_slots = filtered if filtered else slots
        else:
            display_slots = slots

        date_fmt = target_date.strftime("%A, %b %d")
        if not display_slots:
            conv.current_state = ConversationState.COLLECTING_DETAILS
            context["pending_buttons"] = [
                {"id": "check_tomorrow", "title": "Check Tomorrow"},
                {"id": "check_next_day", "title": "Day After Tomorrow"},
            ]
            conv.context = context
            conv.save(update_fields=["current_state", "context"])
            return (
                f"We checked our dental calendar, but there are no open slots on *{date_fmt}*.\n\n"
                f"Would you like to check tomorrow or another day?"
            )

        context["offered_slots"] = display_slots
        context["pending_buttons"] = self._get_representative_buttons(display_slots)
        conv.current_state = ConversationState.OFFERING_SLOTS
        conv.context = context
        conv.save(update_fields=["current_state", "context"])

        return self._format_available_slots_message(display_slots, date_fmt, action="reserve")

    def _advance_booking_flow(self, conv: Conversation, ai_result, message_text: str) -> str:
        clinic = conv.clinic
        context = conv.context or {}

        # Merge extracted preferences into context
        clean_m = (message_text or "").lower().strip()
        if "pref_today" in clean_m or clean_m == "today":
            context["preferred_date"] = "today"
        elif "pref_tomorrow" in clean_m or clean_m == "tomorrow":
            context["preferred_date"] = "tomorrow"
        elif "pref_day_after" in clean_m or clean_m == "day after tomorrow":
            context["preferred_date"] = "day after tomorrow"
        elif ai_result.preferred_date:
            context["preferred_date"] = ai_result.preferred_date

        if ai_result.preferred_time_range:
            context["preferred_time_range"] = ai_result.preferred_time_range
        if ai_result.appointment_type:
            context["appointment_type"] = ai_result.appointment_type

        pref_date_str = context.get("preferred_date")
        pref_time_str = context.get("preferred_time_range")

        # 1. Ask for missing date with quick buttons
        if not pref_date_str:
            conv.current_state = ConversationState.COLLECTING_DETAILS
            context["pending_buttons"] = [
                {"id": "pref_today", "title": "Today"},
                {"id": "pref_tomorrow", "title": "Tomorrow"},
                {"id": "pref_day_after", "title": "Day After Tomorrow"},
            ]
            conv.context = context
            conv.save(update_fields=["current_state", "context"])
            return (
                f"Welcome to *{clinic.name}*! 👋🦷\n\n"
                f"What day would you prefer for your dental appointment?\n"
                f"Tap a button below or type a day:"
            )

        target_date = parse_relative_date(pref_date_str, clinic.timezone)
        if not target_date:
            conv.current_state = ConversationState.COLLECTING_DETAILS
            context["pending_buttons"] = [
                {"id": "pref_today", "title": "Today"},
                {"id": "pref_tomorrow", "title": "Tomorrow"},
            ]
            conv.context = context
            conv.save(update_fields=["current_state", "context"])
            return "Could you please specify a valid date (for example: *Today*, *Tomorrow*, or *YYYY-MM-DD*)?"

        # 2. Directly retrieve real available slots from Calendar & DB
        slots = self.booking_service.get_available_slots(clinic, target_date)
        if not slots:
            conv.current_state = ConversationState.COLLECTING_DETAILS
            context["pending_buttons"] = [
                {"id": "pref_tomorrow", "title": "Check Tomorrow"},
                {"id": "pref_day_after", "title": "Day After Tomorrow"},
            ]
            conv.context = context
            conv.save(update_fields=["current_state", "context"])
            return f"I'm sorry, we do not have any open slots on {target_date.strftime('%A, %b %d')}. Would you like to check another day?"

        # Filter or prioritize slots matching preferred time if user specified one (e.g. morning, evening, 5 PM)
        if pref_time_str:
            filtered_slots = self._filter_slots_by_preference(slots, pref_time_str)
            display_slots = filtered_slots if filtered_slots else slots
        else:
            display_slots = slots

        context["offered_slots"] = display_slots
        context["pending_buttons"] = self._get_representative_buttons(display_slots)
        conv.current_state = ConversationState.OFFERING_SLOTS
        conv.context = context
        conv.save(update_fields=["current_state", "context"])

        return self._format_available_slots_message(display_slots, target_date.strftime("%A, %b %d"), action="book")

    def _advance_reschedule_flow(self, conv: Conversation, ai_result, message_text: str) -> str:
        clinic = conv.clinic
        context = conv.context or {}
        appt_id = context.get("reschedule_appointment_id")
        appt = Appointment.objects.filter(id=appt_id, clinic=clinic).first()
        if not appt:
            conv.current_state = ConversationState.IDLE
            conv.context = {}
            conv.save(update_fields=["current_state", "context"])
            return "We could not find the appointment to reschedule. Would you like to book a new appointment?"

        clean_m = (message_text or "").lower().strip()

        # User clicked "Keep Original" or cancelled reschedule
        if any(w in clean_m for w in ["keep original", "keep current", "cancel reschedule", "keep_current", "confirm_resched_no"]):
            conv.current_state = ConversationState.IDLE
            conv.context = {}
            conv.save(update_fields=["current_state", "context"])
            return "Understood. Your original appointment remains confirmed as scheduled."

        # If user is in confirm_reschedule sub-step
        if context.get("sub_step") == "confirm_reschedule":
            if ai_result.is_explicit_confirmation or is_affirmative(message_text) or "confirm_resched_yes" in clean_m:
                selected_slot = context.get("pending_reschedule_slot")
                if selected_slot:
                    new_start = datetime.fromisoformat(selected_slot["start"])
                    new_end = datetime.fromisoformat(selected_slot["end"])
                    self.booking_service.reschedule_appointment(clinic, appt, new_start, new_end)
                    conv.current_state = ConversationState.IDLE
                    conv.context = {}
                    conv.save(update_fields=["current_state", "context"])
                    display_time = selected_slot.get("display", selected_slot.get("time_formatted"))
                    return (
                        f"🎉 *Appointment Rescheduled Successfully!*\n\n"
                        f"📅 *New Time:* {display_time}\n"
                        f"🏥 *Clinic:* {clinic.name}\n\n"
                        f"Your Google Calendar invite has been updated and the old slot has been released. See you then!"
                    )
            elif is_negative(message_text):
                conv.current_state = ConversationState.IDLE
                conv.context = {}
                conv.save(update_fields=["current_state", "context"])
                return "Rescheduling cancelled. Your original appointment remains confirmed as scheduled."

        # Check if user selected one of the offered slots
        offered_slots = context.get("offered_slots", [])
        if offered_slots:
            selected_slot = self._match_slot_choice(message_text, offered_slots)
            if selected_slot:
                context["pending_reschedule_slot"] = selected_slot
                context["sub_step"] = "confirm_reschedule"
                context["pending_buttons"] = [
                    {"id": "confirm_resched_yes", "title": "Confirm Reschedule"},
                    {"id": "confirm_resched_no", "title": "Keep Original"},
                ]
                conv.context = context
                conv.save(update_fields=["context"])
                display_time = selected_slot.get("display", selected_slot.get("time_formatted"))
                tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
                old_time = appt.starts_at.astimezone(tz).strftime("%I:%M %p on %A, %B %d")
                return (
                    f"Please confirm: Would you like to reschedule your appointment?\n\n"
                    f"🔄 *From:* {old_time}\n"
                    f"👉 *To:* {display_time}\n"
                    f"🏥 *Clinic:* {clinic.name}\n\n"
                    f"Tap *Confirm Reschedule* below or reply *YES* to proceed."
                )

        # User is picking a date or asking for times
        pref_date_str = ai_result.preferred_date or context.get("preferred_date")
        if "resched_today" in clean_m or clean_m == "today":
            pref_date_str = "today"
        elif "resched_tomorrow" in clean_m or "tomorrow" in clean_m:
            pref_date_str = "tomorrow"
        elif "resched_day_after" in clean_m or "day after tomorrow" in clean_m:
            pref_date_str = "day after tomorrow"
        elif not pref_date_str:
            tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
            today = datetime.now(tz).date()
            today_slots = self.booking_service.get_available_slots(clinic, today)
            pref_date_str = "today" if today_slots else "tomorrow"

        context["preferred_date"] = pref_date_str
        target_date = parse_relative_date(pref_date_str, clinic.timezone)
        if not target_date:
            tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
            target_date = datetime.now(tz).date() + timedelta(days=1)

        slots = self.booking_service.get_available_slots(clinic, target_date)
        pref_time = ai_result.preferred_time_range
        if pref_time:
            filtered = self._filter_slots_by_preference(slots, pref_time)
            display_slots = filtered if filtered else slots
        else:
            display_slots = slots

        date_fmt = target_date.strftime("%A, %b %d")
        if not display_slots:
            context["pending_buttons"] = [
                {"id": "resched_tomorrow", "title": "Check Tomorrow"},
                {"id": "resched_day_after", "title": "Day After Tomorrow"},
            ]
            conv.context = context
            conv.save(update_fields=["context"])
            return f"We checked the dental calendar, but there are no open slots on *{date_fmt}*. Would you like to check another day?"

        context["offered_slots"] = display_slots
        context["pending_buttons"] = self._get_representative_buttons(display_slots)
        conv.context = context
        conv.save(update_fields=["context"])

        return self._format_available_slots_message(display_slots, date_fmt, action="reschedule")

    def _match_slot_choice(self, text: str, offered_slots: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not offered_slots:
            return None

        raw = text.strip()
        text_clean = raw.lower()

        # 1. Normalize punctuation in time formats (e.g. "6;30" -> "6:30", "6.30" -> "6:30", "6-30" -> "6:30")
        text_clean = re.sub(r"(\d{1,2})[;.\-](\d{2})", r"\1:\2", text_clean)

        # 2. Normalize time spacing (e.g. "5pm" -> "5 pm", "5:00pm" -> "5:00 pm")
        normalized = re.sub(r"(\d+)\s*(am|pm)", r"\1 \2", text_clean)
        normalized = re.sub(r"[^\w\s:]", " ", normalized)
        normalized = " ".join(normalized.split())

        # PRIORITY 1: Match against exact or partial TIME string FIRST!
        # This guarantees "6;30" or "6:30" is matched to the 6:30 slot and NEVER to slot index #6!
        for slot in offered_slots:
            time_formatted = slot.get("time_formatted", "").lower().strip()
            time_normalized = re.sub(r"(\d+)\s*(am|pm)", r"\1 \2", time_formatted)
            unpadded = time_normalized.lstrip("0")
            simple_hour = re.sub(r":00", "", time_normalized)
            simple_hour_unpadded = re.sub(r":00", "", unpadded)

            patterns = [time_formatted, time_normalized, unpadded, simple_hour, simple_hour_unpadded]
            for p in patterns:
                if p and p in normalized:
                    return slot

            if ":" in time_formatted:
                h_m = unpadded.split(" ")[0]  # e.g. "6:30"
                if h_m in normalized:
                    return slot

            if slot.get("start") and slot["start"] in text_clean:
                return slot

        # PRIORITY 2: Number index (e.g. "1", "2", "3", "slot 1", "option 2", "first", "second")
        # ONLY if the input does NOT specify a colon or minutes (e.g. "6:30" or "6 30" must NOT match index 6)
        if ":" not in text_clean:
            word_nums = ["first", "second", "third", "fourth", "fifth"]
            for idx, slot in enumerate(offered_slots):
                slot_num = str(idx + 1)
                word = word_nums[idx] if idx < len(word_nums) else ""
                num_tokens = [slot_num, f"slot {slot_num}", f"option {slot_num}", f"#{slot_num}", word, f"{word} one"]
                if normalized in num_tokens:
                    return slot
                # Only match prefix if followed by non-numeric tokens (e.g. "1 please", NOT "6 30")
                if re.match(rf"^(?:slot\s+|option\s+|#)?{slot_num}(?:\s+[a-zA-Z]+)*$", normalized):
                    return slot

        # PRIORITY 3: If user typed single hour number like "5" or "6" that matches the hour of a slot
        digits = re.findall(r"\b(\d{1,2})\b", normalized)
        if len(digits) == 1 and ":" not in text_clean:
            hr_val = digits[0]
            for slot in offered_slots:
                tf = slot.get("time_formatted", "")
                if tf.startswith(f"{hr_val.zfill(2)}:00") or tf.startswith(f"{hr_val}:00"):
                    return slot

        return None

    def _filter_slots_by_preference(self, slots: List[Dict[str, Any]], pref: str) -> List[Dict[str, Any]]:
        pref_lower = pref.lower()

        # Morning (before 12 PM), Afternoon (12 PM - 5 PM), Evening (after 5 PM)
        if "morning" in pref_lower:
            return [s for s in slots if "AM" in s.get("time_formatted", "")]
        if "afternoon" in pref_lower:
            return [s for s in slots if "PM" in s.get("time_formatted", "") and any(h in s["time_formatted"] for h in ["12:", "1:", "2:", "3:", "4:", "01:", "02:", "03:", "04:"])]
        if "evening" in pref_lower:
            return [s for s in slots if "PM" in s.get("time_formatted", "") and any(h in s["time_formatted"] for h in ["5:", "6:", "7:", "8:", "05:", "06:", "07:", "08:"])]

        # If specific hour mentioned like "6 pm"
        match = re.search(r"(\d{1,2})", pref_lower)
        if match:
            target_hr = match.group(1)
            target_hr_padded = target_hr.zfill(2)
            matching = [
                s for s in slots
                if s.get("time_formatted", "").startswith(f"{target_hr}:")
                or s.get("time_formatted", "").startswith(f"{target_hr_padded}:")
            ]
            if matching:
                return matching

        return slots

    def _get_reschedule_buttons(self, clinic: Clinic) -> List[Dict[str, str]]:
        """
        Builds reschedule date quick-reply buttons.
        Dynamically includes 'Today' if the clinic has open slots remaining today.
        """
        tz = ZoneInfo(clinic.timezone or "Asia/Kolkata")
        today = datetime.now(tz).date()
        today_slots = self.booking_service.get_available_slots(clinic, today)
        buttons = []
        if today_slots:
            buttons.append({"id": "resched_today", "title": "Today"})
            buttons.append({"id": "resched_tomorrow", "title": "Tomorrow"})
            buttons.append({"id": "keep_current", "title": "Keep Original"})
        else:
            buttons.append({"id": "resched_tomorrow", "title": "Tomorrow"})
            buttons.append({"id": "resched_day_after", "title": "Day After Tomorrow"})
            buttons.append({"id": "keep_current", "title": "Keep Original"})
        return buttons

    def _format_available_slots_message(
        self,
        slots: List[Dict[str, Any]],
        date_str: str,
        action: str = "book",
    ) -> str:
        """
        Formats all available slots categorized by Morning, Afternoon, and Evening.
        Ensures the patient sees every open slot across the clinic's operating hours.
        """
        if not slots:
            return f"There are no available slots on *{date_str}*."

        if len(slots) == 1:
            action_verb = "reschedule to" if action == "reschedule" else "reserve"
            return (
                f"We have 1 slot available on *{date_str}*:\n\n"
                f"👉 1️⃣ *{slots[0]['time_formatted']}*\n\n"
                f"Would you like to {action_verb} this slot? Reply *1* or *YES* to proceed."
            )

        morning_slots = []
        afternoon_slots = []
        evening_slots = []

        for s in slots:
            tf = s.get("time_formatted", "")
            if "AM" in tf:
                morning_slots.append(tf)
            elif any(tf.startswith(h) for h in ["12:", "01:", "1:", "02:", "2:", "03:", "3:", "04:", "4:"]):
                afternoon_slots.append(tf)
            else:
                evening_slots.append(tf)

        sections = []
        if morning_slots:
            sections.append(f"🌅 *Morning:*\n  " + "  •  ".join(morning_slots))
        if afternoon_slots:
            sections.append(f"☀️ *Afternoon:*\n  " + "  •  ".join(afternoon_slots))
        if evening_slots:
            sections.append(f"🌆 *Evening:*\n  " + "  •  ".join(evening_slots))

        slot_body = "\n\n".join(sections)
        action_verb = "reschedule to" if action == "reschedule" else "select"

        return (
            f"Here are the available times on our dental calendar for *{date_str}*:\n\n"
            f"{slot_body}\n\n"
            f"👉 Tap a quick button below or simply reply with any time (e.g. \"{slots[0]['time_formatted']}\") to {action_verb}:"
        )

    def _get_representative_buttons(self, slots: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """
        Creates up to 3 quick-reply buttons (Meta API limit) distributed across open times.
        """
        if not slots:
            return []
        if len(slots) <= 3:
            return [{"id": s["time_formatted"], "title": s["time_formatted"][:20]} for s in slots]

        # Pick representative slots across Morning, Afternoon, Evening
        morning = [s for s in slots if "AM" in s.get("time_formatted", "")]
        afternoon = [s for s in slots if "PM" in s.get("time_formatted", "") and any(s["time_formatted"].startswith(h) for h in ["12:", "01:", "1:", "02:", "2:", "03:", "3:", "04:", "4:"])]
        evening = [s for s in slots if "PM" in s.get("time_formatted", "") and not any(s["time_formatted"].startswith(h) for h in ["12:", "01:", "1:", "02:", "2:", "03:", "3:", "04:", "4:"])]

        chosen = []
        if morning:
            chosen.append(morning[0])
        if afternoon:
            chosen.append(afternoon[0])
        if evening:
            chosen.append(evening[0])

        idx = 0
        while len(chosen) < 3 and idx < len(slots):
            cand = slots[idx]
            if cand not in chosen:
                chosen.append(cand)
            idx += 1

        chosen.sort(key=lambda s: s.get("start", ""))
        return [{"id": s["time_formatted"], "title": s["time_formatted"][:20]} for s in chosen[:3]]

    def _get_active_appointment(self, clinic: Clinic, whatsapp_sender_id: str) -> Optional[Appointment]:
        patient = Patient.objects.filter(clinic=clinic, whatsapp_id=whatsapp_sender_id).first()
        if not patient:
            return None
        return Appointment.objects.filter(
            clinic=clinic,
            patient=patient,
            starts_at__gte=timezone.now(),
        ).exclude(status__in=[Appointment.Status.CANCELLED, Appointment.Status.NO_SHOW]).order_by("starts_at").first()


def is_affirmative(text: str) -> bool:
    clean = re.sub(r"[^\w\s]", " ", text or "").strip().lower()
    t = " ".join(clean.split())
    affirmative_tokens = [
        "yes", "confirm", "confirm_yes", "confirm booking", "yes confirm", "yes please",
        "sure", "yup", "ok confirm", "please confirm", "ok", "1", "yes 1", "confirm it",
        "book it", "yeah", "done", "correct", "perfect", "yep", "go ahead",
        "confirm_resched_yes", "confirm reschedule", "yes cancel", "cancel_yes"
    ]
    return t in affirmative_tokens or t.startswith("yes") or t.startswith("confirm")


def is_negative(text: str) -> bool:
    raw = (text or "").lower().strip()
    if any(pos in raw for pos in ["cancel_yes", "yes cancel", "confirm cancel"]):
        return False
    clean = re.sub(r"[^\w\s]", " ", text or "").strip().lower()
    t = " ".join(clean.split())
    negative_tokens = [
        "no", "cancel", "dont", "don't", "stop", "nevermind",
        "confirm_no", "confirm_resched_no", "cancel_no", "keep original", "keep current", "keep appointment"
    ]
    return t in negative_tokens or t.startswith("no ")


def uuid_suffix() -> str:
    import uuid
    return uuid.uuid4().hex[:6]
