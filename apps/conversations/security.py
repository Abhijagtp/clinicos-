import re
import html
import time
from typing import Tuple, Optional
from django.core.cache import cache

# Prompt injection / system manipulation patterns
PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    r"disregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    r"system\s+prompt",
    r"developer\s+mode",
    r"you\s+are\s+now\s+(?:unrestricted|dan|jailbroken)",
    r"jailbreak",
    r"reveal\s+(?:api\s*key|secret|password|token|instructions)",
    r"show\s+(?:me\s+)?(?:your\s+)?(?:api\s*key|system\s+prompt|instructions)",
    r"drop\s+table",
    r"select\s+\*\s+from",
    r"union\s+select",
    r"exec\s*\(",
    r"<script\b",
]

# Profanity and abusive content keywords
INAPPROPRIATE_KEYWORDS = [
    "fuck", "bitch", "asshole", "bastard", "dick", "pussy", "cunt",
    "slut", "whore", "nigger", "faggot", "kill yourself", "die",
    "shut up", "idiot bot", "scam bot", "fraud"
]

# Medical medication / prescription inquiry patterns
MEDICAL_ADVICE_PATTERNS = [
    r"\b(?:what|which)\s+(?:medicine|tablet|drug|antibiotic|painkiller)\b",
    r"\b(?:prescribe|prescription)\b",
    r"\b(?:amoxicillin|ibuprofen|paracetamol|combiflam|augmentin|tylenol|aspirin)\b",
    r"\b(?:can\s+i\s+take|dosage\s+for|how\s+much\s+mg)\b",
]


def is_prompt_injection(text: str) -> bool:
    """
    Detects attempts to manipulate, jailbreak, or extract system instructions.
    """
    if not text:
        return False
    lower_text = text.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, lower_text):
            return True
    return False


def is_inappropriate_content(text: str) -> bool:
    """
    Detects abusive, vulgar, or offensive content.
    """
    if not text:
        return False
    # Normalize punctuation and extra spaces
    clean = re.sub(r"[^\w\s]", " ", text.lower())
    tokens = set(clean.split())
    for kw in INAPPROPRIATE_KEYWORDS:
        if " " in kw:
            if kw in clean:
                return True
        elif kw in tokens:
            return True
    return False


def is_medical_prescription_inquiry(text: str) -> bool:
    """
    Detects if the user is asking for medication prescriptions or medical advice.
    """
    if not text:
        return False
    lower_text = text.lower()
    for pattern in MEDICAL_ADVICE_PATTERNS:
        if re.search(pattern, lower_text):
            return True
    return False


def validate_patient_name(raw_name: str) -> Tuple[bool, Optional[str]]:
    """
    Validates and sanitizes a patient's name.
    - Strips common prefixes ("my name is", "this is", etc.)
    - Rejects strings with HTML, script tags, SQL symbols, or non-name characters
    - Must be between 2 and 60 characters
    """
    if not raw_name:
        return False, None

    # Strip conversational prefixes
    cleaned = re.sub(
        r"^(?:my name is|i am|name:?|this is|call me)\s*",
        "",
        raw_name.strip(),
        flags=re.IGNORECASE,
    ).strip()

    # Reject dangerous HTML/SQL injection characters
    if any(char in cleaned for char in ["<", ">", ";", '"', "\\", "/", "{", "}", "=", "$", "%", "*"]):
        return False, None

    # Name must only contain letters, spaces, hyphens, periods, or apostrophes
    if not re.match(r"^[a-zA-Z\s\.\-']{2,60}$", cleaned):
        return False, None

    # Must contain at least two alphabetic characters
    letters_only = re.sub(r"[^a-zA-Z]", "", cleaned)
    if len(letters_only) < 2:
        return False, None

    def title_piece(p: str) -> str:
        return "-".join([sub.capitalize() for sub in p.split("-")])

    title_cased = " ".join([title_piece(part) for part in cleaned.split()])
    return True, title_cased


def validate_patient_email(raw_email: str) -> Tuple[bool, Optional[str]]:
    """
    Validates an email address format strictly.
    """
    if not raw_email:
        return False, None

    cleaned = raw_email.strip().lower()
    # RFC-compliant simple email regex
    email_regex = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    if len(cleaned) > 100 or not re.match(email_regex, cleaned):
        return False, None

    return True, cleaned


def check_rate_limit(sender_id: str, max_messages: int = 15, window_seconds: int = 60) -> bool:
    """
    Sliding window rate-limiter using Django cache.
    Returns True if allowed, False if throttled.
    """
    cache_key = f"rl_{sender_id}"
    now = time.time()
    try:
        history = cache.get(cache_key) or []
        # Keep only timestamps within window
        history = [ts for ts in history if now - ts < window_seconds]

        if len(history) >= max_messages:
            return False

        history.append(now)
        cache.set(cache_key, history, timeout=window_seconds)
        return True
    except Exception:
        # If cache backend fails, fail open to avoid rejecting valid patients
        return True
