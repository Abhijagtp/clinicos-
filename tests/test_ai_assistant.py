import pytest
from unittest.mock import patch, MagicMock
from apps.ai_assistant.schemas import BookingIntentExtraction
from apps.ai_assistant.service import MockAIAssistant, GeminiAIAssistant, get_ai_assistant


class TestAIAssistant:

    def test_mock_ai_extracts_booking_intent_and_relative_date(self):
        assistant = MockAIAssistant()
        message = "Hi, I want to book a dentist appointment tomorrow evening around 6 PM"
        result = assistant.extract_booking_intent(message)

        assert isinstance(result, BookingIntentExtraction)
        assert result.intent == "book"
        assert result.preferred_date == "tomorrow"
        assert result.preferred_time_range == "6:00 PM"
        assert result.is_explicit_confirmation is False
        assert result.needs_human_escalation is False

    def test_mock_ai_extracts_patient_name(self):
        assistant = MockAIAssistant()
        message = "My name is Priya Nair and I need a checkup next Monday"
        result = assistant.extract_booking_intent(message)

        assert result.intent == "book"
        assert result.patient_name == "Priya Nair"
        assert result.preferred_date == "next Monday"

    def test_mock_ai_detects_explicit_confirmation(self):
        assistant = MockAIAssistant()
        for confirm_msg in ["yes", "confirm", "yes please", "sure", "ok confirm"]:
            result = assistant.extract_booking_intent(confirm_msg)
            assert result.is_explicit_confirmation is True

    def test_mock_ai_detects_human_escalation_and_emergency(self):
        assistant = MockAIAssistant()
        result = assistant.extract_booking_intent("I am having unbearable toothache bleeding emergency")
        assert result.needs_human_escalation is True

        result_human = assistant.extract_booking_intent("Can I speak to a receptionist or human?")
        assert result_human.needs_human_escalation is True

    def test_gemini_service_fallback_on_api_exception(self, settings):
        settings.GEMINI_API_KEY = "dummy_real_looking_api_key_for_test"
        assistant = GeminiAIAssistant(api_key="dummy_real_looking_api_key_for_test")

        # Mock the google.genai Client to simulate an API exception/timeout
        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.models.generate_content.side_effect = Exception("API rate limit exceeded or quota exhausted")
            mock_client_cls.return_value = mock_client

            # Should not crash; gracefully returns fallback extraction
            result = assistant.extract_booking_intent("I want an appointment tomorrow at 5 PM")
            assert isinstance(result, BookingIntentExtraction)
            assert result.intent == "book"
            assert result.preferred_date == "tomorrow"

    def test_gemini_service_uses_structured_output_when_successful(self):
        assistant = GeminiAIAssistant(api_key="dummy_key")

        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.text = '{"intent": "book", "preferred_date": "2026-10-15", "preferred_time_range": "10:00 AM", "missing_information": [], "is_explicit_confirmation": false, "needs_human_escalation": false}'
            mock_client.models.generate_content.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = assistant.extract_booking_intent("Book an appointment for 2026-10-15 at 10 AM")
            assert result.intent == "book"
            assert result.preferred_date == "2026-10-15"
            assert result.preferred_time_range == "10:00 AM"
