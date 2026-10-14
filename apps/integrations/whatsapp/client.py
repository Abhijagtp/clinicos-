import logging
import requests
from typing import Dict, Any, Optional, List
from django.conf import settings

logger = logging.getLogger(__name__)


class WhatsAppClientError(Exception):
    """Base exception for WhatsApp Graph API communication errors."""
    pass


class WhatsAppClient:
    """
    Client for interacting with the Meta WhatsApp Cloud (Graph) API.
    Does not log tokens or sensitive patient PII.
    """

    def __init__(
        self,
        access_token: Optional[str] = None,
        phone_number_id: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout: int = 10,
    ):
        self.access_token = access_token or getattr(settings, "WHATSAPP_ACCESS_TOKEN", "")
        self.phone_number_id = phone_number_id or getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "")
        self.api_version = api_version or getattr(settings, "WHATSAPP_API_VERSION", "v21.0")
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}"

    def send_text_message(self, recipient_id: str, text: str) -> Dict[str, Any]:
        """
        Sends a plain text message to a WhatsApp recipient.
        """
        if not self.access_token or not self.phone_number_id:
            logger.warning("WhatsApp credentials not set; outbound message simulated in development.")
            return {"messages": [{"id": f"simulated_{recipient_id}"}], "messaging_product": "whatsapp"}

        url = f"{self.base_url}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_id,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }

        masked_recipient = f"{recipient_id[:3]}***{recipient_id[-2:]}" if len(recipient_id) > 5 else "***"
        logger.info(f"Sending WhatsApp message to {masked_recipient}")

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            if not response.ok:
                error_data = {}
                try:
                    error_data = response.json()
                except Exception:
                    error_data = {"error": response.text}
                logger.error(f"WhatsApp API responded with status {response.status_code}")
                raise WhatsAppClientError(f"WhatsApp API error {response.status_code}: {error_data.get('error', {}).get('message', response.text)}")

            return response.json()
        except requests.RequestException as exc:
            logger.error(f"Network error sending WhatsApp message: {exc.__class__.__name__}")
            raise WhatsAppClientError(f"Network error communicating with WhatsApp: {exc}") from exc

    def send_interactive_buttons(
        self,
        recipient_id: str,
        body_text: str,
        buttons: List[Dict[str, str]],
        header_text: Optional[str] = None,
        footer_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Sends an interactive quick-reply button message (up to 3 buttons supported by Meta).
        Falls back seamlessly to text message if buttons exceed 3 or on API restriction.
        """
        if not self.access_token or not self.phone_number_id:
            logger.warning("WhatsApp credentials not set; outbound buttons simulated.")
            return self.send_text_message(recipient_id, body_text)

        if not buttons or len(buttons) > 3:
            return self.send_text_message(recipient_id, body_text)

        button_actions = []
        for btn in buttons[:3]:
            title = str(btn.get("title", ""))[:20]
            btn_id = str(btn.get("id", title))[:256]
            button_actions.append({
                "type": "reply",
                "reply": {
                    "id": btn_id,
                    "title": title,
                }
            })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_id,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {"buttons": button_actions},
            },
        }
        if header_text:
            payload["interactive"]["header"] = {"type": "text", "text": header_text[:60]}
        if footer_text:
            payload["interactive"]["footer"] = {"text": footer_text[:60]}

        url = f"{self.base_url}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            if response.ok:
                return response.json()
            logger.warning(f"Interactive button API error {response.status_code}: {response.text}. Falling back to plain text.")
            return self.send_text_message(recipient_id, body_text)
        except Exception as exc:
            logger.warning(f"Network error sending interactive buttons ({exc}); falling back to plain text.")
            return self.send_text_message(recipient_id, body_text)
