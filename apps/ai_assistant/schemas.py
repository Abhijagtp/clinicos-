from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class BookingIntentExtraction(BaseModel):
    intent: Literal[
        "book",
        "reschedule",
        "cancel",
        "availability_inquiry",
        "clinic_faq",
        "human_support",
        "greeting",
        "unknown",
    ] = Field(
        default="unknown",
        description="The primary intent of the patient message."
    )
    patient_name: Optional[str] = Field(
        default=None,
        description="Patient name if provided in message."
    )
    patient_email: Optional[str] = Field(
        default=None,
        description="Patient email address if provided in message."
    )
    preferred_date: Optional[str] = Field(
        default=None,
        description="Extracted preferred date, relative (e.g., 'tomorrow', 'next Monday') or absolute (e.g., '2026-10-13')."
    )
    preferred_time_range: Optional[str] = Field(
        default=None,
        description="Extracted preferred time or period, e.g., '6:00 PM', 'morning', 'after 5 PM'."
    )
    appointment_type: Optional[str] = Field(
        default=None,
        description="Dental service or reason, e.g., 'checkup', 'cleaning', 'toothache', 'consultation'."
    )
    missing_information: List[str] = Field(
        default_factory=list,
        description="List of required details still missing (e.g., 'date', 'time', 'confirmation')."
    )
    is_explicit_confirmation: bool = Field(
        default=False,
        description="True if the patient explicitly confirmed a proposed appointment slot (e.g., 'Yes', 'Confirm', 'Yes please')."
    )
    is_cancellation_confirmation: bool = Field(
        default=False,
        description="True if the patient explicitly confirmed cancelling an appointment."
    )
    needs_human_escalation: bool = Field(
        default=False,
        description="True if the message is a medical emergency, complex clinical inquiry, or patient asked for a receptionist."
    )
    conversational_reply: Optional[str] = Field(
        default=None,
        description="Suggested polite response for non-booking inquiries or greetings. Medical diagnoses prohibited."
    )
