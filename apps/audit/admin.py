from django.contrib import admin
from .models import AuditEvent

@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "clinic", "actor", "action", "object_type", "object_id")
    list_filter = ("clinic", "action", "object_type")
    readonly_fields = ("id", "clinic", "actor", "action", "object_type", "object_id", "metadata", "request_id", "created_at")
