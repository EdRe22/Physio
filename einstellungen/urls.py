from django.urls import path

from rechnungen import views as billing

from . import views

urlpatterns = [
    path("", views.index, name="einstellungen"),
    path("profil/", billing.profile_edit, name="profile_edit"),
    path("profil/logo/", billing.profile_logo, name="profile_logo"),
    path("mahnwesen/", billing.reminder_settings, name="reminder_settings"),
    path("mailserver/", billing.mail_server, name="mail_server"),
    path("farbe/", views.design, name="my_design"),
    path("konto/", views.account, name="my_account"),
]
