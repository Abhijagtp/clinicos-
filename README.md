# Clinic OS — Dedicated AI-Powered WhatsApp Booking & Clinic Engine

**Clinic OS** is a dedicated, production-ready backend engine built specifically for private dental clinics. It connects **Meta WhatsApp Cloud API**, **Conversational AI** (OpenRouter / Google Gemini), and **Google Calendar** to deliver a seamless, automated patient booking experience.

Unlike generic multi-tenant SaaS platforms, this system is architected as a **dedicated deployment for a specific clinic**, offering deep customization, direct calendar synchronization, zero multi-tenant overhead, and strict data privacy.

---

## 🌟 Key Features

### 1. Conversational AI Chatbot & WhatsApp Interactive Buttons
* **Natural Dialogue**: Understands informal greetings, common queries, and typos (e.g. typing `6;30`, `6.30`, or `6:30 pm` accurately maps to 06:30 PM).
* **Comprehensive Schedule Display**: Displays open clinic slots organized by time of day:
  * 🌅 **Morning** (e.g. 09:00 AM, 09:30 AM, 10:00 AM, 11:30 AM)
  * ☀️ **Afternoon** (e.g. 01:00 PM, 02:30 PM, 03:00 PM, 04:30 PM)
  * 🌆 **Evening** (e.g. 05:00 PM, 06:00 PM, 06:30 PM)
* **Interactive Quick Replies**: Sends Meta WhatsApp interactive buttons while allowing patients to tap or type any open slot.
* **Flexible Confirmation**: Allows patients to switch slots or ask about other dates during the confirmation step without getting trapped in rigid loops.

### 2. Live Google Calendar Synchronization
* **Calendar as Scheduling Truth**: Slots are generated dynamically from clinic operating hours minus booked database appointments and live Google Calendar busy intervals in `Asia/Kolkata`.
* **Automatic Patient Calendar Invites**: When an appointment is confirmed, the patient's email is added as an attendee (`sendUpdates="all"`), automatically adding the event to the patient's personal Google Calendar.
* **Rescheduling & Cancellation in Sync**: Rescheduling updates the calendar event in real time. Cancellations immediately release the slot on Google Calendar.
* **Automatic Lifecycle Signals & Reconciliation**: A Django `post_delete` signal removes events if an appointment is deleted. A built-in `py manage.py sync_calendar` command audits and cleans orphaned events.

### 3. Concurrency Protection & Zero Double-Booking
* **Atomic Reservations (`BookingReservation`)**: Slots are locked with a 10-minute TTL in an atomic PostgreSQL transaction before calling external APIs.
* **Two-Phase Commit & Compensation**: If external calendar calls fail, the temporary database reservation is rolled back automatically.

### 4. Docker & Production Ready
* **Docker Container**: Multi-stage `Dockerfile` running Python 3.11 with an unprivileged non-root user and Gunicorn WSGI.
* **Docker Compose**: Pre-configured `docker-compose.yml` for running the backend and an optional local PostgreSQL database.

---

## 🏗️ Architecture

```mermaid
flowchart TD
    Patient([WhatsApp Patient]) <--> Meta[Meta WhatsApp Cloud API]
    Meta <--> Webhook[Django Webhook /api/v1/whatsapp/webhook/]
    Webhook --> Deduplicator[Message Deduplicator & Audit Log]
    Deduplicator --> Orchestrator[Conversation State Machine]
    Orchestrator <--> AI[AI Assistant Engine\nOpenRouter / Gemini / Offline]
    Orchestrator <--> BookingSvc[Booking Domain Service]
    BookingSvc <--> DB[(PostgreSQL Database)]
    BookingSvc <--> GCal[Clinic Google Calendar & Patient Invites]
    Staff([Clinic Staff]) <--> StaffAPI[REST APIs /api/v1/appointments/]
    StaffAPI --> DB
```

---

## 🛠️ Tech Stack

- **Framework**: Django 5.2, Django REST Framework
- **Language**: Python 3.10+ / 3.11
- **Database**: PostgreSQL (psycopg 3) / SQLite (offline tests)
- **Messaging**: Meta WhatsApp Cloud API (Graph API v21.0)
- **AI / LLM**: OpenRouter API (`google/gemma-4-31b-it:free`, `nvidia/nemotron`) & Google Gemini API (`google-genai` SDK)
- **Calendar**: Google Calendar API (`google-api-python-client` with OAuth 2.0)
- **WSGI Server**: Gunicorn
- **Containerization**: Docker & Docker Compose
- **Testing**: `pytest`, `pytest-django` (41 automated tests)

---

## 📁 Project Structure

```text
backend/
├── apps/
│   ├── clinics/               # Clinic configuration, hours (Mon-Sat 9 AM - 7 PM), timezone
│   ├── patients/              # Patient records, WhatsApp identifiers, contact details
│   ├── appointments/          # Appointments, BookingReservation, signals, sync command
│   │   └── management/commands/sync_calendar.py  # DB <-> Google Calendar sync command
│   ├── conversations/         # Orchestrator state machine, conversation context, message logs
│   ├── integrations/
│   │   ├── whatsapp/          # Webhook security (SHA256 HMAC), parser, interactive buttons client
│   │   └── google_calendar/   # Google OAuth 2.0 service, attendee sync, FreeBusy engine
│   ├── ai_assistant/          # Dual AI engine: OpenRouter + Gemini + offline fallback
│   ├── accounts/              # Clinic staff accounts
│   ├── consultations/         # Clinical records
│   └── audit/                 # Security audit event logging
├── services/
│   └── booking_service.py     # Pure domain service coordinating transactions, reservations & sync
├── tests/                     # 41 comprehensive tests (100% offline with mocks)
├── config/                    # Django settings, URLs, WSGI, ASGI
├── Dockerfile                 # Production Docker image with non-root security
├── docker-compose.yml         # Container orchestration
├── .dockerignore              # Docker build exclusions
├── .gitignore                 # Comprehensive git exclusions protecting secrets
├── .env.example               # Safe environment template
├── requirements.txt           # Python dependencies
└── manage.py
```

---

## 🚀 Getting Started

### 1. Clone & Set Up Environment
```bash
git clone https://github.com/Abhijagtp/clinicos-.git
cd clinicos-/backend

# Create virtual environment
python -m venv venv

# Activate (Windows)
venv\Scripts\activate
# Activate (Linux/macOS)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Configure your credentials in `.env`:
```env
DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,*
DATABASE_URL=postgresql://user:password@host/neondb?sslmode=require

# WhatsApp Cloud API
WHATSAPP_ACCESS_TOKEN=your_meta_token
WHATSAPP_PHONE_NUMBER_ID=your_phone_id
WHATSAPP_VERIFY_TOKEN=your_verify_token
WHATSAPP_APP_SECRET=your_app_secret

# AI Providers
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODEL=google/gemma-4-31b-it:free
GEMINI_API_KEY=your_gemini_key

# Google Calendar
GOOGLE_CALENDAR_ID=your_calendar_id@group.calendar.google.com
GOOGLE_CLIENT_ID=your_oauth_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_oauth_client_secret
GOOGLE_REFRESH_TOKEN=your_oauth_refresh_token
CLINIC_TIMEZONE=Asia/Kolkata
```

### 3. Run Database Migrations
```bash
python manage.py migrate
```

### 4. Run Automated Tests
```bash
pytest
```
*All 41 tests execute 100% offline using mock adapters without consuming live API tokens.*

### 5. Start Development Server
```bash
python manage.py runserver 0.0.0.0:8000
```

---

## 🐳 Docker Deployment

### Run with Docker Compose
```bash
# Build and launch containers
docker compose up -d --build

# View container logs
docker compose logs -f backend

# Stop containers
docker compose down
```

### Build and Run Docker Container Directly
```bash
docker build -t clinic-os-backend .
docker run -d -p 8000:8000 --env-file .env --name clinic_os clinic-os-backend
```

---

## 🔄 Google Calendar Reconciliation Command

To audit and synchronize Google Calendar with your PostgreSQL database:

```bash
# Reconcile and purge any orphaned calendar events
python manage.py sync_calendar

# Dry-run preview without making changes
python manage.py sync_calendar --dry-run
```

---

## 🔒 Security & Best Practices

- **Zero Secret Commits**: Sensitive credentials (`.env`, `credentials*.json`, `*.pem`, `*.key`) are excluded by `.gitignore` and `.dockerignore`.
- **HMAC Verification**: Every inbound Meta webhook payload is validated using SHA256 HMAC against `WHATSAPP_APP_SECRET`.
- **Clinical Safety Rules**: Built-in guardrails prevent medical diagnosis, strictly routing emergency cases to human clinic reception.
