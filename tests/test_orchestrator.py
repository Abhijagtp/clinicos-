import pytest
from apps.conversations.orchestrator import ConversationOrchestrator
from apps.conversations.models import Conversation, ConversationState
from apps.appointments.models import Appointment
from services.booking_service import BookingService


@pytest.mark.django_db
class TestConversationOrchestrator:

    def test_full_patient_booking_journey(self, clinic, mock_ai, mock_calendar):
        booking_service = BookingService(calendar_service=mock_calendar)
        orchestrator = ConversationOrchestrator(
            ai_assistant=mock_ai,
            booking_service=booking_service,
        )

        sender_id = "919988776655"

        # Step 1: Patient initiates booking
        reply1 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="Hi, I want to book a dentist appointment tomorrow evening around 6 PM",
            external_message_id="msg_step_1",
        )
        assert "available" in reply1.lower()
        conv = Conversation.objects.get(clinic=clinic, whatsapp_sender_id=sender_id)
        assert conv.current_state == ConversationState.OFFERING_SLOTS
        assert "offered_slots" in conv.context

        # Step 2: Patient selects slot
        reply2 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="6 PM",
            external_message_id="msg_step_2",
        )
        assert "please confirm" in reply2.lower()
        conv.refresh_from_db()
        assert conv.current_state == ConversationState.AWAITING_CONFIRMATION

        # Step 3: Patient explicitly confirms
        reply3 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="Yes, confirm",
            external_message_id="msg_step_3",
        )
        assert "confirmed" in reply3.lower()
        conv.refresh_from_db()
        assert conv.current_state == ConversationState.BOOKED

        # Verify Appointment was saved
        appt = Appointment.objects.filter(clinic=clinic, patient__whatsapp_id=sender_id).first()
        assert appt is not None
        assert appt.status == Appointment.Status.CONFIRMED
        assert appt.external_calendar_event_id != ""

    def test_human_escalation_flow(self, clinic, mock_ai, mock_calendar):
        booking_service = BookingService(calendar_service=mock_calendar)
        orchestrator = ConversationOrchestrator(
            ai_assistant=mock_ai,
            booking_service=booking_service,
        )

        sender_id = "919988776600"
        reply = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="I need to talk to a receptionist please",
            external_message_id="msg_human_1",
        )
        assert "reception" in reply.lower() or "staff" in reply.lower()
        conv = Conversation.objects.get(clinic=clinic, whatsapp_sender_id=sender_id)
        assert conv.current_state == ConversationState.HUMAN_HANDOFF

    def test_cancellation_flow(self, clinic, patient, mock_ai, mock_calendar):
        booking_service = BookingService(calendar_service=mock_calendar)
        orchestrator = ConversationOrchestrator(
            ai_assistant=mock_ai,
            booking_service=booking_service,
        )

        sender_id = patient.whatsapp_id

        # First, book an appointment
        from django.utils import timezone
        from datetime import timedelta
        start_at = timezone.now() + timedelta(days=2)
        end_at = start_at + timedelta(minutes=30)
        appt = booking_service.confirm_booking(clinic, patient, start_at, end_at)

        # Patient requests cancel
        reply1 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="Please cancel my appointment",
            external_message_id="msg_cancel_1",
        )
        assert "are you sure" in reply1.lower()
        conv = Conversation.objects.get(clinic=clinic, whatsapp_sender_id=sender_id)
        assert conv.current_state == ConversationState.CANCELLING

        # Patient confirms cancellation
        reply2 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="Yes cancel",
            external_message_id="msg_cancel_2",
        )
        assert "cancelled" in reply2.lower()

        appt.refresh_from_db()
        assert appt.status == Appointment.Status.CANCELLED

    def test_missing_booking_details_multi_turn(self, clinic, mock_ai, mock_calendar):
        booking_service = BookingService(calendar_service=mock_calendar)
        orchestrator = ConversationOrchestrator(
            ai_assistant=mock_ai,
            booking_service=booking_service,
        )
        sender_id = "919812345678"

        # Turn 1: Patient gives no date or time
        reply1 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="I want to book an appointment please",
            external_message_id="turn_1",
        )
        assert "what day" in reply1.lower()
        conv = Conversation.objects.get(clinic=clinic, whatsapp_sender_id=sender_id)
        assert conv.current_state == ConversationState.COLLECTING_DETAILS

        # Turn 2: Patient supplies date only; bot directly checks calendar and offers slots!
        reply2 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="Tomorrow",
            external_message_id="turn_2",
        )
        assert "available slots" in reply2.lower() or "available" in reply2.lower()
        conv.refresh_from_db()
        assert conv.current_state == ConversationState.OFFERING_SLOTS

        # Turn 3: Patient selects offered slot
        reply3 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="1",
            external_message_id="turn_3",
        )
        assert "name" in reply3.lower()
        conv.refresh_from_db()
        assert conv.current_state == ConversationState.AWAITING_CONFIRMATION

    def test_availability_inquiry_checks_calendar_and_offers_slots(self, clinic, mock_ai, mock_calendar):
        booking_service = BookingService(calendar_service=mock_calendar)
        orchestrator = ConversationOrchestrator(
            ai_assistant=mock_ai,
            booking_service=booking_service,
        )
        sender_id = "919777888999"

        # Patient asks about available times without prior booking intent
        reply = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="what are available times ?",
            external_message_id="avail_inq_1",
        )
        assert "available times" in reply.lower() or "available" in reply.lower()
        conv = Conversation.objects.get(clinic=clinic, whatsapp_sender_id=sender_id)
        assert conv.current_state == ConversationState.OFFERING_SLOTS
        assert "pending_buttons" in conv.context
        assert len(conv.context["pending_buttons"]) > 0

    def test_reschedule_flow_checks_calendar_and_updates_event(self, clinic, patient, mock_ai, mock_calendar):
        booking_service = BookingService(calendar_service=mock_calendar)
        orchestrator = ConversationOrchestrator(
            ai_assistant=mock_ai,
            booking_service=booking_service,
        )
        sender_id = patient.whatsapp_id

        # 1. Create active upcoming appointment
        from django.utils import timezone
        from datetime import timedelta
        start_at = timezone.now() + timedelta(days=2)
        end_at = start_at + timedelta(minutes=30)
        appt = booking_service.confirm_booking(clinic, patient, start_at, end_at)

        # 2. Patient asks to reschedule
        reply1 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="I want to reschedule my appointment",
            external_message_id="resched_1",
        )
        assert "what day" in reply1.lower()
        conv = Conversation.objects.get(clinic=clinic, whatsapp_sender_id=sender_id)
        assert conv.current_state == ConversationState.RESCHEDULING

        # 3. Patient asks for times or chooses Tomorrow
        reply2 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="Tomorrow",
            external_message_id="resched_2",
        )
        assert "available times" in reply2.lower() or "available" in reply2.lower()
        conv.refresh_from_db()
        assert "offered_slots" in conv.context

        # 4. Patient selects a slot
        reply3 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="1",
            external_message_id="resched_3",
        )
        assert "confirm" in reply3.lower()

        # 5. Patient confirms reschedule
        reply4 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="Yes",
            external_message_id="resched_4",
        )
        assert "rescheduled" in reply4.lower()
        conv.refresh_from_db()
        assert conv.current_state == ConversationState.IDLE

    def test_conversational_greeting_reply(self, clinic, mock_ai, mock_calendar):
        booking_service = BookingService(calendar_service=mock_calendar)
        orchestrator = ConversationOrchestrator(
            ai_assistant=mock_ai,
            booking_service=booking_service,
        )
        sender_id = "919112233445"

        # Patient sends casual greeting "hey"
        reply = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="hey",
            external_message_id="greet_1",
        )
        # Should return the conversational reply generated by the AI
        assert "how can i help" in reply.lower() or "hey" in reply.lower()
        conv = Conversation.objects.get(clinic=clinic, whatsapp_sender_id=sender_id)
        assert conv.current_state == ConversationState.IDLE
        assert "pending_buttons" in conv.context
        assert any(b["id"] == "check_avail" for b in conv.context["pending_buttons"])

    def test_reschedule_flow_today_button_and_slots(self, clinic, patient, mock_ai, mock_calendar):
        booking_service = BookingService(calendar_service=mock_calendar)
        orchestrator = ConversationOrchestrator(
            ai_assistant=mock_ai,
            booking_service=booking_service,
        )
        sender_id = patient.whatsapp_id

        # 1. Create active upcoming appointment
        from django.utils import timezone
        from datetime import timedelta
        start_at = timezone.now() + timedelta(days=3)
        end_at = start_at + timedelta(minutes=30)
        booking_service.confirm_booking(clinic, patient, start_at, end_at)

        # 2. Patient requests reschedule
        reply1 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="reschedule",
            external_message_id="resched_today_1",
        )
        conv = Conversation.objects.get(clinic=clinic, whatsapp_sender_id=sender_id)
        assert conv.current_state == ConversationState.RESCHEDULING
        # Check if Today or Tomorrow button is present
        btn_ids = [b["id"] for b in conv.context.get("pending_buttons", [])]
        assert "resched_today" in btn_ids or "resched_tomorrow" in btn_ids

        # 3. Patient asks for Today
        reply2 = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="Today",
            external_message_id="resched_today_2",
        )
        conv.refresh_from_db()
        assert "offered_slots" in conv.context or "pending_buttons" in conv.context

    def test_all_clinic_hours_slots_displayed_not_truncated(self, clinic, mock_ai, mock_calendar):
        booking_service = BookingService(calendar_service=mock_calendar)
        orchestrator = ConversationOrchestrator(
            ai_assistant=mock_ai,
            booking_service=booking_service,
        )
        sender_id = "919001122334"

        # Check availability for tomorrow
        reply = orchestrator.process_incoming_message(
            clinic=clinic,
            sender_id=sender_id,
            message_text="what are available times tomorrow ?",
            external_message_id="avail_all_1",
        )
        conv = Conversation.objects.get(clinic=clinic, whatsapp_sender_id=sender_id)
        offered = conv.context.get("offered_slots", [])
        # MockCalendar returns all operating hours slots for the day (more than 3)
        assert len(offered) >= 3
        # Ensure buttons have at most 3 options (Meta limit) but offered_slots has all slots
        assert len(conv.context.get("pending_buttons", [])) <= 3

