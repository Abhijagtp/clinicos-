import os
import json
import logging
from typing import Dict, Any
from django.conf import settings

logger = logging.getLogger(__name__)


def generate_soap_note_from_text(raw_text: str, patient_context: Dict[str, Any] = None) -> Dict[str, str]:
    """
    Takes raw dictation transcript or doctor's bullet points and parses
    or synthesizes into structured SOAP format:
      - Subjective: Chief complaint, history, patient-reported symptoms
      - Objective: Clinical findings, tooth numbers, examination notes
      - Assessment: Clinical impression, diagnoses
      - Plan: Proposed treatment, procedures, prescriptions, next visit
    """
    raw_text = (raw_text or "").strip()
    patient_context = patient_context or {}

    gemini_key = getattr(settings, "GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    openrouter_key = getattr(settings, "OPENROUTER_API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")

    # Try Gemini or OpenRouter if configured
    if gemini_key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=gemini_key)
            prompt = f"""You are an expert dental scribe and clinical assistant.
Convert the following dental consultation transcript/notes into structured SOAP format.
Patient Context: {json.dumps(patient_context)}
Raw Clinical Notes / Transcript:
{raw_text}

Return JSON with exact keys:
"subjective": string,
"objective": string,
"assessment": string,
"plan": string
"""
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            data = json.loads(response.text)
            return {
                "subjective": data.get("subjective", ""),
                "objective": data.get("objective", ""),
                "assessment": data.get("assessment", ""),
                "plan": data.get("plan", ""),
            }
        except Exception as e:
            logger.warning("Gemini SOAP note generation failed, using rule-based extraction: %s", e)

    # Deterministic fallback parser / structured converter
    return _rule_based_soap_parser(raw_text)


def _rule_based_soap_parser(text: str) -> Dict[str, str]:
    """
    Fallback deterministic parser that intelligently breaks down common dental terms
    into Subjective, Objective, Assessment, and Plan sections.
    """
    subjective_lines = []
    objective_lines = []
    assessment_lines = []
    plan_lines = []

    # Support continuous speech dictation without manual newlines
    if "\n" in text:
        lines = [line.strip() for line in text.split("\n") if line.strip()]
    else:
        import re
        lines = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    current_section = "subjective"

    for line in lines:
        lower = line.lower()
        if lower.startswith(("s:", "subjective:", "complaint:", "history:")):
            current_section = "subjective"
            cleaned = line.split(":", 1)[1].strip()
            if cleaned:
                subjective_lines.append(cleaned)
        elif lower.startswith(("o:", "objective:", "exam:", "finding:", "intraoral:", "teeth:", "tooth:")):
            current_section = "objective"
            cleaned = line.split(":", 1)[1].strip()
            if cleaned:
                objective_lines.append(cleaned)
        elif lower.startswith(("a:", "assessment:", "diagnosis:", "dx:", "impression:")):
            current_section = "assessment"
            cleaned = line.split(":", 1)[1].strip()
            if cleaned:
                assessment_lines.append(cleaned)
        elif lower.startswith(("p:", "plan:", "treatment:", "rx:", "prescription:", "followup:", "next:")):
            current_section = "plan"
            cleaned = line.split(":", 1)[1].strip()
            if cleaned:
                plan_lines.append(cleaned)
        else:
            # Categorize based on keywords if not explicit
            if any(k in lower for k in ["pain", "hurt", "ache", "reported", "complains", "sensitivity", "swelling since"]):
                subjective_lines.append(line)
            elif any(k in lower for k in ["decay", "cavity", "probe", "mm pocket", "bleeding on probing", "caries", "fracture line", "#", "tooth"]):
                objective_lines.append(line)
            elif any(k in lower for k in ["pulpitis", "gingivitis", "periodontitis", "abscess", "necrosis", "calculus"]):
                assessment_lines.append(line)
            elif any(k in lower for k in ["prescribe", "amoxicillin", "ibuprofen", "rct", "filling", "composite", "crown", "scaling", "recall", "extract"]):
                plan_lines.append(line)
            else:
                if current_section == "subjective":
                    subjective_lines.append(line)
                elif current_section == "objective":
                    objective_lines.append(line)
                elif current_section == "assessment":
                    assessment_lines.append(line)
                else:
                    plan_lines.append(line)

    return {
        "subjective": "\n".join(subjective_lines) if subjective_lines else text,
        "objective": "\n".join(objective_lines) if objective_lines else "Clinical intraoral examination performed.",
        "assessment": "\n".join(assessment_lines) if assessment_lines else "Dental evaluation conducted.",
        "plan": "\n".join(plan_lines) if plan_lines else "Treatment options discussed with patient.",
    }
