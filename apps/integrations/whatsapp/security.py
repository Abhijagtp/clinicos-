import hmac
import hashlib
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def verify_meta_signature(raw_body: bytes, signature_header: str, app_secret: str = None) -> bool:
    """
    Verifies that the incoming webhook payload was sent by Meta using HMAC-SHA256.
    Header format: sha256=<hex_digest>
    """
    secret = app_secret or getattr(settings, "WHATSAPP_APP_SECRET", "")
    if not secret:
        logger.warning("WHATSAPP_APP_SECRET not configured. Signature verification will fail.")
        return False

    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("Missing or malformed X-Hub-Signature-256 header.")
        return False

    expected_sig = signature_header.split("sha256=", 1)[1].strip()
    calculated_sig = hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_sig, calculated_sig)
