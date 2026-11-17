from django.urls import path

from . import views

urlpatterns = [
    path("", views.import_start, name="import_start"),
    path("vorlage/<str:profile>/", views.import_template, name="import_template"),
    path("<int:pk>/zuordnung/", views.import_mapping, name="import_mapping"),
    path("<int:pk>/vorschau/", views.import_preview, name="import_preview"),
    path("<int:pk>/ausfuehren/", views.import_run, name="import_run"),
    path("<int:pk>/abbrechen/", views.import_cancel, name="import_cancel"),
]
