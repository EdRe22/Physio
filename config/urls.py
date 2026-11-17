from django.shortcuts import redirect
from django.urls import include, path

from core.jobs import run_job
from core.unlock import unlock_view

urlpatterns = [
    path("entsperren/", unlock_view, name="unlock"),
    path("intern/job/<str:name>/", run_job, name="run_job"),
    path("", lambda request: redirect("patient_list")),
    path("", include("accounts.urls")),
    path("patients/", include("patients.urls")),
    path("termine/", include("termine.urls")),
    path("import/", include("importer.urls")),
    path("rechnungen/", include("rechnungen.urls")),
    path("buchhaltung/", include("buchhaltung.urls")),
    path("praxis/", include("praxis.urls")),
]
