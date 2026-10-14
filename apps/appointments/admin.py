from django.contrib import admin
from .models import Appointment

@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("patient", "provider", "starts_at", "ends_at", "status", "clinic")
    list_filter = ("clinic", "status")
    search_fields = ("patient__first_name", "patient__last_name", "reason")
