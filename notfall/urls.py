from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="notfall_index"),
    path("<int:pk>/freigeben/", views.approve, name="notfall_approve"),
    path("<int:pk>/beenden/", views.end, name="notfall_end"),
    path("<int:pk>/patienten/", views.patients, name="notfall_patients"),
    path("<int:pk>/patient/<int:patient_pk>/", views.patient, name="notfall_patient"),
]
