import json
import logging
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

from .security import verify_meta_signature
from .parser import WhatsAppWebhookParser
from .client import WhatsAppClient, WhatsAppClientError
from apps.clinics.models import Clinic
from apps.conversations.models import Conversation
from apps.conversations.orchestrator import ConversationOrchestrator

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class WhatsAppWebhookView(APIView):
    """
    Meta WhatsApp Cloud API Webhook endpoint.
    Handles verification challenges (GET) and incoming messages/statuses (POST).
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        """
        Meta Webhook verification handshake.
        """
        mode = request.GET.get("hub.mode") or request.GET.get("hub_mode")
        token = (request.GET.get("hub.verify_token") or request.GET.get("hub_verify_token") or "").strip()
        challenge = request.GET.get("hub.challenge") or request.GET.get("hub_challenge")

        configured_token = str(getattr(settings, "WHATSAPP_VERIFY_TOKEN", "")).strip()

        if mode == "subscribe":
            if token and token == configured_token:
                logger.info("WhatsApp webhook challenge verified successfully.")
                return HttpResponse(challenge, content_type="text/plain", status=200)
            logger.warning("WhatsApp webhook challenge verification failed: token mismatch (got '%s', expected '%s').", token, configured_token)
            return HttpResponse("Verification token mismatch", status=403)

        # Friendly response when opened directly in a browser
        return JsonResponse({
            "status": "active",
            "service": "Clinic OS WhatsApp Webhook",
            "message": "Webhook endpoint is active and listening for Meta WhatsApp events.",
        }, status=200)

    def post(self, request, *args, **kwargs):
        """
        Processes incoming WhatsApp webhook payloads with HMAC-SHA256 signature verification.
        """
        signature = request.headers.get("X-Hub-Signature-256", "")
        raw_body = request.body

        # Verify Meta HMAC-SHA256 signature
        if not verify_meta_signature(raw_body, signature):
            logger.warning("Rejected webhook request with invalid Meta signature.")
            return HttpResponse("Invalid signature", status=403)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error("Malformed JSON payload in webhook request.")
            return HttpResponse("Malformed payload", status=400)

        parsed = WhatsAppWebhookParser.parse_payload(payload)

        # 1. Delivery status updates
        if parsed.get("statuses") and not parsed.get("messages"):
            # Acknowledge delivery statuses promptly without triggering conversational logic
            return JsonResponse({"status": "acknowledged_status"}, status=200)

        # 2. Resolve target Clinic
        phone_number_id = parsed.get("phone_number_id")
        clinic = None
        if phone_number_id:
            from apps.integrations.whatsapp.models import WhatsAppConfig
            config = WhatsAppConfig.objects.filter(phone_number_id=phone_number_id, is_active=True).first()
            if config:
                clinic = config.clinic

        if not clinic:
            clinic = Clinic.objects.filter(status=Clinic.Status.ACTIVE).first()

        if not clinic:
            logger.error("No active clinic configured to handle WhatsApp message.")
            return JsonResponse({"status": "error", "message": "No active clinic found"}, status=200)

        client = WhatsAppClient()
        orchestrator = ConversationOrchestrator()

        # 3. Process each incoming message
        for msg in parsed.get("messages", []):
            sender_id = msg.sender_id
            msg_id = msg.message_id
            msg_type = msg.message_type

            if msg_type not in ["text", "interactive"]:
                # Graceful handling for non-text messages
                try:
                    client.send_text_message(
                        recipient_id=sender_id,
                        text="Thank you for reaching out to Clinic OS Dental. We currently only accept text messages on WhatsApp. Please send a text with your appointment request.",
                    )
                except WhatsAppClientError as exc:
                    logger.error("Failed to send non-text message notice to %s: %s", sender_id, exc)
                continue

            # Process message through state machine orchestrator
            reply_text = orchestrator.process_incoming_message(
                clinic=clinic,
                sender_id=sender_id,
                message_text=msg.text_body or "",
                external_message_id=msg_id,
            )

            # Check if interactive buttons were queued for this reply
            conv = Conversation.objects.filter(clinic=clinic, whatsapp_sender_id=sender_id).first()
            buttons = None
            if conv and conv.context.get("pending_buttons"):
                buttons = conv.context.pop("pending_buttons")
                conv.save(update_fields=["context"])

            if reply_text:
                try:
                    if buttons:
                        client.send_interactive_buttons(recipient_id=sender_id, body_text=reply_text, buttons=buttons)
                    else:
                        client.send_text_message(recipient_id=sender_id, text=reply_text)
                except WhatsAppClientError as exc:
                    logger.error("Failed to send WhatsApp reply to %s: %s", sender_id, exc)

        return JsonResponse({"status": "success"}, status=200)
