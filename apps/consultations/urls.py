from django.urls import path
from .views import (
    ConsultationListCreateView,
    ConsultationDetailView,
    ClinicalNoteCreateUpdateView,
    ClinicalNoteFinalizeView,
    AIGenerateSoapView,
)

urlpatterns = [
    path("", ConsultationListCreateView.as_view(), name="consultation-list-create"),
    path("<uuid:id>/", ConsultationDetailView.as_view(), name="consultation-detail"),
    path("<uuid:id>/notes/", ClinicalNoteCreateUpdateView.as_view(), name="consultation-notes-update"),
    path("<uuid:id>/notes/<uuid:note_id>/finalize/", ClinicalNoteFinalizeView.as_view(), name="consultation-note-finalize"),
    path("<uuid:id>/generate-soap/", AIGenerateSoapView.as_view(), name="consultation-generate-soap"),
]
