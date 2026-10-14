from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class IncomingWhatsAppMessage:
    message_id: str
    sender_id: str
    message_type: str
    text_body: Optional[str] = None
    timestamp: Optional[str] = None
    raw_message: Optional[Dict[str, Any]] = None


@dataclass
class IncomingWhatsAppStatus:
    status_id: str
    recipient_id: str
    status: str  # sent, delivered, read, failed
    timestamp: Optional[str] = None


class WhatsAppWebhookParser:
    """
    Parses Meta WhatsApp Cloud API webhook JSON payloads.
    Distinguishes between incoming patient messages and delivery statuses.
    """

    @staticmethod
    def parse_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parses the payload and returns:
        {
            "messages": [IncomingWhatsAppMessage, ...],
            "statuses": [IncomingWhatsAppStatus, ...],
            "phone_number_id": str | None
        }
        """
        result = {
            "messages": [],
            "statuses": [],
            "phone_number_id": None,
        }

        entry_list = payload.get("entry", [])
        for entry in entry_list:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                metadata = value.get("metadata", {})
                phone_number_id = metadata.get("phone_number_id")
                if phone_number_id:
                    result["phone_number_id"] = str(phone_number_id)

                # 1. Incoming messages
                messages = value.get("messages", [])
                for msg in messages:
                    msg_id = msg.get("id")
                    sender_id = msg.get("from")
                    msg_type = msg.get("type", "unknown")
                    text_body = None
                    if msg_type == "text":
                        text_body = msg.get("text", {}).get("body", "").strip()
                    elif msg_type == "interactive":
                        interactive_obj = msg.get("interactive", {})
                        if interactive_obj.get("type") == "button_reply":
                            text_body = interactive_obj.get("button_reply", {}).get("title", "").strip()
                        elif interactive_obj.get("type") == "list_reply":
                            text_body = interactive_obj.get("list_reply", {}).get("title", "").strip()

                    if msg_id and sender_id:
                        result["messages"].append(
                            IncomingWhatsAppMessage(
                                message_id=str(msg_id),
                                sender_id=str(sender_id),
                                message_type=msg_type,
                                text_body=text_body,
                                timestamp=msg.get("timestamp"),
                                raw_message=msg,
                            )
                        )

                # 2. Status notifications (sent, delivered, read)
                statuses = value.get("statuses", [])
                for st in statuses:
                    st_id = st.get("id")
                    recipient_id = st.get("recipient_id")
                    status_val = st.get("status")
                    if st_id and status_val:
                        result["statuses"].append(
                            IncomingWhatsAppStatus(
                                status_id=str(st_id),
                                recipient_id=str(recipient_id),
                                status=status_val,
                                timestamp=st.get("timestamp"),
                            )
                        )

        return result
