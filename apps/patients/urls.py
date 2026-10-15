from django.urls import path
from apps.patients.views import (
    PatientListCreateView,
    PatientDetailView,
    PatientTimelineView,
)

urlpatterns = [
    path("", PatientListCreateView.as_view(), name="patient-list-create"),
    path("<uuid:id>/", PatientDetailView.as_view(), name="patient-detail"),
    path("<uuid:id>/timeline/", PatientTimelineView.as_view(), name="patient-timeline"),
]
