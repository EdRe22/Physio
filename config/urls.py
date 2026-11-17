from django.shortcuts import redirect
from django.urls import include, path

urlpatterns = [
    path("", lambda request: redirect("patient_list")),
    path("", include("accounts.urls")),
    path("patients/", include("patients.urls")),
    path("termine/", include("termine.urls")),
    path("import/", include("importer.urls")),
    path("rechnungen/", include("rechnungen.urls")),
    path("buchhaltung/", include("buchhaltung.urls")),
    path("praxis/", include("praxis.urls")),
]
