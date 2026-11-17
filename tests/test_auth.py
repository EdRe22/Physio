from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import Therapist
from audit.models import AuditEntry
from core import tenancy
from tests.conftest import totp_token

PASSWORD = "ein-langes-passwort-123"


def test_anonymous_is_redirected_to_login():
    response = Client().get("/patients/")
    assert response.status_code == 302 and "/login/" in response["Location"]


def test_password_alone_is_not_enough_without_device(user_alpha):
    client = Client()
    assert client.post("/login/", {"username": "alpha_user", "password": PASSWORD}).status_code == 302
    response = client.get("/patients/")
    assert response.status_code == 302 and response["Location"].endswith("/2fa/setup/")


def test_password_alone_is_not_enough_with_device(user_alpha):
    TOTPDevice.objects.create(user=user_alpha, name="default", confirmed=True)
    client = Client()
    client.post("/login/", {"username": "alpha_user", "password": PASSWORD})
    response = client.get("/patients/")
    assert response.status_code == 302 and response["Location"].endswith("/2fa/")


def test_full_login_with_otp_and_audit(user_alpha, alpha):
    device = TOTPDevice.objects.create(user=user_alpha, name="default", confirmed=True)
    client = Client()
    client.post("/login/", {"username": "alpha_user", "password": PASSWORD})
    assert client.post("/2fa/", {"token": "000000"}).status_code == 200  # falscher Code
    assert client.get("/patients/").status_code == 302
    # Nach einem Fehlversuch ist das Gerät gedrosselt: auch der richtige Code wird sofort danach abgelehnt
    assert client.post("/2fa/", {"token": totp_token(device)}).status_code == 200
    device.refresh_from_db()
    device.throttle_reset()  # im Test die Wartezeit überspringen
    assert client.post("/2fa/", {"token": totp_token(device)}).status_code == 302
    assert client.get("/patients/").status_code == 200
    with tenancy.tenant_context(alpha):
        assert AuditEntry.objects.filter(action="auth.login").count() == 1


def test_setup_flow_enables_2fa(user_alpha):
    client = Client()
    client.post("/login/", {"username": "alpha_user", "password": PASSWORD})
    assert client.get("/2fa/setup/").status_code == 200
    device = TOTPDevice.objects.get(user=user_alpha, confirmed=False)
    assert client.post("/2fa/setup/", {"token": totp_token(device)}).status_code == 302
    assert client.get("/patients/").status_code == 200


def test_cannot_add_second_device_with_password_only(user_alpha):
    TOTPDevice.objects.create(user=user_alpha, name="default", confirmed=True)
    client = Client()
    client.post("/login/", {"username": "alpha_user", "password": PASSWORD})
    response = client.get("/2fa/setup/")
    assert response.status_code == 302 and response["Location"].endswith("/2fa/")
    assert TOTPDevice.objects.filter(user=user_alpha).count() == 1


def test_lockout_after_failed_logins(user_alpha):
    client = Client()
    for _ in range(5):
        client.post("/login/", {"username": "alpha_user", "password": "falsch"})
    user = Therapist.objects.get(pk=user_alpha.pk)
    assert user.locked_until and user.locked_until > timezone.now()
    # Selbst mit richtigem Passwort bleibt das Konto gesperrt
    client.post("/login/", {"username": "alpha_user", "password": PASSWORD})
    assert client.get("/patients/").status_code == 302
    assert "/login/" in client.get("/patients/")["Location"]


def test_security_headers_and_no_cache(client_alpha):
    response = client_alpha.get("/patients/")
    assert response["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response["Content-Security-Policy"]
    assert response["X-Frame-Options"] == "DENY"
