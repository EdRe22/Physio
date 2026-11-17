from django.conf import settings
from django.shortcuts import redirect
from django.views.static import serve
from django.urls import include, path

from core.dashboard import uebersicht
from core.jobs import run_job
from core.unlock import unlock_view

urlpatterns = [
    path("static/<path:path>", serve, {"document_root": settings.BASE_DIR / "static"}, name="static_file"),
    path("entsperren/", unlock_view, name="unlock"),
    path("intern/job/<str:name>/", run_job, name="run_job"),
    path("uebersicht/", uebersicht, name="uebersicht"),
    path("", lambda request: redirect("uebersicht")),
    path("", include("accounts.urls")),
    path("patients/", include("patients.urls")),
    path("termine/", include("termine.urls")),
    path("import/", include("importer.urls")),
    path("rechnungen/", include("rechnungen.urls")),
    path("buchhaltung/", include("buchhaltung.urls")),
    path("praxis/", include("praxis.urls")),
    path("notfall/", include("notfall.urls")),
    path("raeume/", include("raeume.urls")),
    path("hilfe/", include("hilfe.urls")),
    path("verwaltung/", include("verwaltung.urls")),
    path("einstellungen/", include("einstellungen.urls")),
]
