# Clinic OS Architecture Documentation

## 1. System Overview

Clinic OS is designed as a modular, domain-driven Django backend prioritizing reliability, concurrency safety, and privacy for Indian outpatient clinics.

### Domain Boundaries
- `apps.clinics`: Tenancy boundary. Clinics define operating schedules, timezone (`Asia/Kolkata`), slot durations, and staff memberships.
- `apps.patients`: Patient identity management within a clinic.
- `apps.appointments`: Scheduling truth, `Appointment` records, and ephemeral `BookingReservation` locks.
- `apps.conversations`: WhatsApp state machine, conversation context, and message audit logs.
- `apps.integrations.whatsapp`: Transport layer for Meta WhatsApp Cloud API.
- `apps.integrations.google_calendar`: Transport and synchronization layer for Google Calendar.
- `apps.ai_assistant`: Language understanding provider adapter (Gemini API with fallback).
- `services.booking_service`: Pure domain service coordinating reservations, database transactions, calendar synchronization, and compensation.

---

## 2. Concurrency & Double-Booking Prevention

### The Double-Booking Hazard
When multiple patients request the same slot simultaneously via WhatsApp, naive calendar checks cause race conditions where both patients receive a confirmation.

### The Clinic OS Protocol:
1. **Candidate Slot Generation**: The calendar service fetches busy intervals from Google Calendar FreeBusy and combines them with active database bookings.
2. **Atomic Reservation (`BookingReservation`)**: Before calling external APIs, an atomic database transaction checks for existing appointments or reservations overlapping with the proposed interval:
   ```sql
   SELECT id FROM appointments_appointment
   WHERE clinic_id = :clinic_id
     AND starts_at < :end_at AND ends_at > :start_at
     AND status NOT IN ('cancelled', 'no_show');
   ```
3. **Idempotency Keying**: If no conflict exists, a `BookingReservation` is created with a 10-minute expiry time (`expires_at`) and an idempotency key.
4. **External Synchronization**: With the slot reserved in the DB, the service calls Google Calendar `events().insert()`.
5. **Final Confirmation**: A confirmed `Appointment` is saved in the database, the reservation status changes to `CONFIRMED`, and an `AuditEvent` is generated.
6. **Compensation on Failure**: If the Google Calendar call fails or times out, the `BookingReservation` is immediately set to `RELEASED`. If the calendar event was created but the database write fails, the orphan Google Calendar event is deleted immediately.

---

## 3. Conversation State Machine Transitions

| Current State | Inbound Trigger | Next State | Action / Outbound Response |
|---|---|---|---|
| `IDLE` | Greeting / Hi | `IDLE` | Welcome message |
| `IDLE` | Book appointment (missing details) | `COLLECTING_DETAILS` | Asks for missing date or time |
| `IDLE` | Book appointment (date + time given) | `OFFERING_SLOTS` | Fetches real calendar slots; offers 2-3 slots |
| `COLLECTING_DETAILS` | Provides date | `COLLECTING_DETAILS` | Asks for preferred time |
| `COLLECTING_DETAILS` | Provides time | `OFFERING_SLOTS` | Fetches real calendar slots; offers slots |
| `OFFERING_SLOTS` | Selects slot (e.g. "6 PM") | `AWAITING_CONFIRMATION` | Prompts: "Please confirm: appointment on [Date] at [Time]. Reply 'Yes' to confirm." |
| `AWAITING_CONFIRMATION` | Explicit "Yes" / "Confirm" | `BOOKED` -> `IDLE` | Executes atomic booking + GCal event; sends confirmation |
| `AWAITING_CONFIRMATION` | Decline / "No" | `IDLE` | Cancels draft flow |
| `IDLE` / Any | Cancel request | `CANCELLING` | Locates upcoming appointment; asks for confirmation |
| `CANCELLING` | Explicit "Yes cancel" | `IDLE` | Cancels appointment; deletes calendar event |
| `IDLE` / Any | Reschedule request | `RESCHEDULING` | Prompts for new preferred date/time |
| Any | Emergency / Receptionist request | `HUMAN_HANDOFF` | Escalates to clinic staff; sends emergency safety note |

---

## 4. Multi-Tenant Clinic Isolation

All data queries for patients, appointments, and conversations are strictly scoped by `clinic_id`:
- Clinic staff users are linked via `ClinicMembership`.
- Staff API endpoints filter all appointment listings with `clinic_id__in=authorized_clinic_ids`.
- Cross-clinic access attempts immediately return HTTP `403 Forbidden`.
- Each clinic can configure their own Google Calendar and WhatsApp credentials.
