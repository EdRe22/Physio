from django.urls import path

from . import views

urlpatterns = [
    path("", views.patient_list, name="patient_list"),
    path("neu/", views.patient_create, name="patient_create"),
    path("<int:pk>/", views.patient_detail, name="patient_detail"),
    path("<int:pk>/bearbeiten/", views.patient_edit, name="patient_edit"),
    path("<int:pk>/archivieren/", views.patient_archive, name="patient_archive"),
    path("<int:patient_pk>/zuweisung/neu/", views.referral_create, name="referral_create"),
    path("<int:patient_pk>/eintrag/neu/", views.entry_create, name="entry_create"),
    path("eintrag/<int:pk>/", views.entry_edit, name="entry_edit"),
    path("<int:pk>/einwilligung/<str:kind>/erteilen/", views.consent_grant, name="consent_grant"),
    path("<int:pk>/einwilligung/<str:kind>/widerrufen/", views.consent_revoke, name="consent_revoke"),
]
