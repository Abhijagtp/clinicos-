import json
import pytest
from unittest.mock import patch
from django.urls import reverse
from tests.conftest import generate_meta_signature
from apps.conversations.models import Conversation, MessageLog, ProcessingStatus
from apps.integrations.whatsapp.client import WhatsAppClient, WhatsAppClientError


@pytest.mark.django_db
class TestWhatsAppWebhook:

    def test_webhook_verification_success(self, api_client, settings):
        settings.WHATSAPP_VERIFY_TOKEN = "test_verify_token_123"
        url = reverse("whatsapp-webhook")
        response = api_client.get(url, {
            "hub.mode": "subscribe",
            "hub.verify_token": "test_verify_token_123",
            "hub.challenge": "challenge_token_abc_456",
        })
        assert response.status_code == 200
        assert response.content.decode("utf-8") == "challenge_token_abc_456"

    def test_webhook_verification_failure_invalid_token(self, api_client, settings):
        settings.WHATSAPP_VERIFY_TOKEN = "correct_token"
        url = reverse("whatsapp-webhook")
        response = api_client.get(url, {
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "challenge_456",
        })
        assert response.status_code == 403

    def test_webhook_post_rejects_invalid_signature(self, api_client, settings):
        settings.WHATSAPP_APP_SECRET = "secret_key_123"
        url = reverse("whatsapp-webhook")
        payload = json.dumps({"entry": []}).encode("utf-8")

        response = api_client.post(
            url,
            data=payload,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256=invalid_hex_digest",
        )
        assert response.status_code == 403

    def test_webhook_post_processes_text_message(self, api_client, settings, clinic):
        settings.WHATSAPP_APP_SECRET = "secret_key_123"
        url = reverse("whatsapp-webhook")

        payload_dict = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "entry_id_1",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"phone_number_id": "100200300"},
                        "messages": [{
                            "from": "919876543210",
                            "id": "wamid.HBgLMTIzNDU2",
                            "timestamp": "1710000000",
                            "text": {"body": "Hi, I want to book an appointment tomorrow evening"},
                            "type": "text",
                        }],
                    },
                }],
            }],
        }
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        sig = generate_meta_signature(payload_bytes, secret="secret_key_123")

        with patch("apps.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_send, \
             patch("apps.conversations.orchestrator.ConversationOrchestrator._handle_state_transition", return_value="Booking received!"):
            mock_send.return_value = {"messages": [{"id": "outbound_1"}]}

            response = api_client.post(
                url,
                data=payload_bytes,
                content_type="application/json",
                HTTP_X_HUB_SIGNATURE_256=sig,
            )

            assert response.status_code == 200
            assert mock_send.called
            recipient = mock_send.call_args[1]["recipient_id"]
            assert recipient == "919876543210"

        # Check message was logged in DB
        assert MessageLog.objects.filter(external_message_id="wamid.HBgLMTIzNDU2").exists()

    def test_webhook_post_idempotency_ignores_duplicates(self, api_client, settings, clinic):
        settings.WHATSAPP_APP_SECRET = "secret_key_123"
        url = reverse("whatsapp-webhook")

        payload_dict = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "919876543210",
                            "id": "wamid.DUPLICATE_TEST_1",
                            "text": {"body": "Hi there"},
                            "type": "text",
                        }],
                    },
                }],
            }],
        }
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        sig = generate_meta_signature(payload_bytes, secret="secret_key_123")

        with patch("apps.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_send, \
             patch("apps.conversations.orchestrator.ConversationOrchestrator._handle_state_transition", return_value="Hi there!"):
            # First delivery
            res1 = api_client.post(url, data=payload_bytes, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)
            assert res1.status_code == 200
            assert mock_send.call_count == 1

            # Second duplicate delivery
            res2 = api_client.post(url, data=payload_bytes, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)
            assert res2.status_code == 200
            # Should NOT have sent another message
            assert mock_send.call_count == 1

    def test_webhook_post_status_notification_acknowledged_silently(self, api_client, settings, clinic):
        settings.WHATSAPP_APP_SECRET = "secret_key_123"
        url = reverse("whatsapp-webhook")

        payload_dict = {
            "entry": [{
                "changes": [{
                    "value": {
                        "statuses": [{
                            "id": "wamid.OUTBOUND_123",
                            "status": "delivered",
                            "timestamp": "1710000000",
                            "recipient_id": "919876543210",
                        }],
                    },
                }],
            }],
        }
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        sig = generate_meta_signature(payload_bytes, secret="secret_key_123")

        with patch("apps.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_send:
            response = api_client.post(url, data=payload_bytes, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)
            assert response.status_code == 200
            # No outbound reply generated for delivery status update
            assert not mock_send.called

    def test_webhook_post_handles_non_text_message(self, api_client, settings, clinic):
        settings.WHATSAPP_APP_SECRET = "secret_key_123"
        url = reverse("whatsapp-webhook")

        payload_dict = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "919876543210",
                            "id": "wamid.IMAGE_MSG_1",
                            "type": "image",
                            "image": {"id": "img123"},
                        }],
                    },
                }],
            }],
        }
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        sig = generate_meta_signature(payload_bytes, secret="secret_key_123")

        with patch("apps.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_send:
            response = api_client.post(url, data=payload_bytes, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)
            assert response.status_code == 200
            assert mock_send.called
            sent_text = mock_send.call_args[1]["text"]
            assert "text messages" in sent_text.lower()

    def test_whatsapp_client_error_handling(self):
        client = WhatsAppClient(access_token="invalid_token", phone_number_id="12345")
        with patch("requests.post") as mock_post:
            mock_post.return_value.ok = False
            mock_post.return_value.status_code = 401
            mock_post.return_value.json.return_value = {"error": {"message": "Invalid OAuth token"}}

            with pytest.raises(WhatsAppClientError) as exc_info:
                client.send_text_message(recipient_id="919876543210", text="Test message")
            assert "401" in str(exc_info.value)

    def test_patient_consent_and_communication_preferences(self, clinic):
        from services.booking_service import BookingService
        service = BookingService()
        patient = service.get_or_create_patient(
            clinic=clinic,
            whatsapp_id="919111122223",
            name="Rahul Sen",
        )
        assert patient.consent_status is True
        assert patient.whatsapp_id == "919111122223"
        assert patient.name == "Rahul Sen"

    def test_webhook_post_sends_interactive_buttons(self, api_client, settings, clinic):
        settings.WHATSAPP_APP_SECRET = "secret_key_123"
        url = reverse("whatsapp-webhook")

        payload_dict = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "919876543210",
                            "id": "wamid.BTN_TEST_OUTBOUND",
                            "text": {"body": "book appointment"},
                            "type": "text",
                        }],
                    },
                }],
            }],
        }
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        sig = generate_meta_signature(payload_bytes, secret="secret_key_123")

        test_buttons = [{"id": "slot_1", "title": "Tomorrow 10:00 AM"}]

        def fake_transition(conv, msg_text):
            conv.context["pending_buttons"] = test_buttons
            conv.save(update_fields=["context"])
            return "Please choose a slot:"

        with patch("apps.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons") as mock_buttons, \
             patch("apps.conversations.orchestrator.ConversationOrchestrator._handle_state_transition", side_effect=fake_transition):
            mock_buttons.return_value = {"messages": [{"id": "btn_msg_1"}]}
            response = api_client.post(url, data=payload_bytes, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)
            assert response.status_code == 200
            assert mock_buttons.called
            assert mock_buttons.call_args[1]["recipient_id"] == "919876543210"
            assert mock_buttons.call_args[1]["buttons"] == test_buttons

    def test_webhook_post_processes_interactive_button_reply(self, api_client, settings, clinic):
        settings.WHATSAPP_APP_SECRET = "secret_key_123"
        url = reverse("whatsapp-webhook")

        payload_dict = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "919876543210",
                            "id": "wamid.BTN_CLICK_1",
                            "type": "interactive",
                            "interactive": {
                                "type": "button_reply",
                                "button_reply": {
                                    "id": "slot_1",
                                    "title": "Tomorrow 10:00 AM",
                                },
                            },
                        }],
                    },
                }],
            }],
        }
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        sig = generate_meta_signature(payload_bytes, secret="secret_key_123")

        with patch("apps.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_send, \
             patch("apps.conversations.orchestrator.ConversationOrchestrator._handle_state_transition", return_value="Slot selected!") as mock_orch:
            mock_send.return_value = {"messages": [{"id": "outbound_btn_reply"}]}
            response = api_client.post(url, data=payload_bytes, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)
            assert response.status_code == 200
            assert mock_orch.called
            # The incoming text should be the button title
            assert mock_orch.call_args[0][1] == "Tomorrow 10:00 AM"
