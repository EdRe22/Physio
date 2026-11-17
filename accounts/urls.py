from django.contrib.auth.views import LoginView
from django.urls import path

from . import views

urlpatterns = [
    path("login/", LoginView.as_view(template_name="accounts/login.html", redirect_authenticated_user=True), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("2fa/", views.otp_verify, name="otp_verify"),
    path("2fa/setup/", views.otp_setup, name="otp_setup"),
]
