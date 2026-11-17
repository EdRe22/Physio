from django.urls import path

from . import views

urlpatterns = [
    path("", views.week, name="termine_week"),
    path("neu/", views.termin_create, name="termin_create"),
    path("<int:pk>/aendern/", views.termin_edit, name="termin_edit"),
    path("<int:pk>/status/", views.termin_status, name="termin_status"),
]
