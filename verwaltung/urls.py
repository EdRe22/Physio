from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="verwaltung_index"),
    path("benutzer/", views.users, name="verwaltung_users"),
    path("benutzer/neu/", views.user_new, name="verwaltung_user_new"),
    path("benutzer/<int:pk>/", views.user_detail, name="verwaltung_user"),
    path("benutzer/<int:pk>/passwort/", views.user_password, name="verwaltung_user_password"),
    path("benutzer/<int:pk>/zweiter-faktor/", views.user_2fa, name="verwaltung_user_2fa"),
    path("benutzer/<int:pk>/sperren/", views.user_lock, name="verwaltung_user_lock"),
    path("benutzer/<int:pk>/ausscheiden/", views.user_offboard, name="verwaltung_user_offboard"),
    path("protokoll-pruefen/", views.audit_check, name="verwaltung_audit_check"),
]
