from django.contrib import admin
from .models import Consultation, ClinicalNote

@admin.register(Consultation)
class ConsultationAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "doctor", "status", "started_at", "clinic")
    list_filter = ("clinic", "status")

@admin.register(ClinicalNote)
class ClinicalNoteAdmin(admin.ModelAdmin):
    list_display = ("id", "consultation", "status", "ai_generated", "created_at", "clinic")
    list_filter = ("clinic", "status", "ai_generated")
