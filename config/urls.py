from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include

from apps.integrations.whatsapp.views import WhatsAppWebhookView
from apps.integrations.google_calendar.views import GoogleCalendarConnectView, GoogleCalendarCallbackView
from apps.appointments.views import (
    AppointmentListCreateView,
    AvailabilityView,
    AppointmentRescheduleView,
    AppointmentCancelView,
)


def health_check(request):
    return JsonResponse({
        "status": "healthy",
        "service": "Clinic OS Backend",
    })


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", health_check, name="health-check"),

    # Authentication & Staff Identity
    path("api/v1/auth/", include("apps.accounts.urls")),

    # WhatsApp Cloud API Webhook
    path("api/v1/whatsapp/webhook/", WhatsAppWebhookView.as_view(), name="whatsapp-webhook"),

    # Google Calendar OAuth Integration
    path("api/v1/integrations/google-calendar/connect/", GoogleCalendarConnectView.as_view(), name="google-calendar-connect"),
    path("api/v1/integrations/google-calendar/callback/", GoogleCalendarCallbackView.as_view(), name="google-calendar-callback"),

    # Appointments API
    path("api/v1/appointments/", AppointmentListCreateView.as_view(), name="appointments-list-create"),
    path("api/v1/appointments/availability/", AvailabilityView.as_view(), name="appointments-availability"),
    path("api/v1/appointments/<uuid:id>/reschedule/", AppointmentRescheduleView.as_view(), name="appointment-reschedule"),
    path("api/v1/appointments/<uuid:id>/cancel/", AppointmentCancelView.as_view(), name="appointment-cancel"),
]
