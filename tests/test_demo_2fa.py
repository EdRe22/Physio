"""Ausnahme vom 2FA-Zwang: nur Demo-Konten, nur mit PHYSIO_DEMO_OHNE_2FA."""
import pytest
from django.test import Client, override_settings
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import Therapist
from core.demo import DEMO_USERNAMES, demo_email
from core.management.commands.create_demo import PEOPLE

PASSWORD = "ein-langes-passwort-123"


def make(tenant, username, email=None):
    return Therapist.objects.create_user(username=username, password=PASSWORD, tenant=tenant,
                                         email=demo_email(username) if email is None else email)


def login(user):
    client = Client()
    client.post("/login/", {"username": user.username, "password": PASSWORD})
    return client


def test_demo_list_matches_create_demo():
    assert tuple(p[2] for p in PEOPLE) == DEMO_USERNAMES


def test_demo_user_needs_second_factor_by_default(alpha):
    response = login(make(alpha, "maria.muster")).get("/")
    assert response.status_code == 302 and response.url == "/2fa/setup/"


@override_settings(PHYSIO_DEMO_OHNE_2FA=True)
def test_demo_user_gets_in_without_second_factor(alpha):
    client = login(make(alpha, "maria.muster"))
    for path in ("/uebersicht/", "/patients/"):
        assert client.get(path).status_code == 200, path


@override_settings(PHYSIO_DEMO_OHNE_2FA=True)
@pytest.mark.parametrize("username,email", [
    ("alpha_user", None),                          # anderer Benutzername
    ("maria.muster", "maria@praxis.example.at"),   # Demo-Name, aber echte Adresse
    ("maria.muster", ""),
])
def test_other_accounts_still_need_second_factor(alpha, username, email):
    response = login(make(alpha, username, email)).get("/patients/")
    assert response.status_code == 302 and response.url == "/2fa/setup/"


@override_settings(PHYSIO_DEMO_OHNE_2FA=True)
def test_voluntarily_configured_factor_is_enforced(alpha):
    user = make(alpha, "tom.testmann")
    TOTPDevice.objects.create(user=user, name="default", confirmed=True)
    response = login(user).get("/patients/")
    assert response.status_code == 302 and response.url == "/2fa/"


@override_settings(PHYSIO_DEMO_OHNE_2FA=True)
def test_password_is_still_required(alpha):
    make(alpha, "maria.muster")
    client = Client()
    client.post("/login/", {"username": "maria.muster", "password": "falsch"})
    response = client.get("/patients/")
    assert response.status_code == 302 and response.url.startswith("/login/")
