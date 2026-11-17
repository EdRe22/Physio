import pytest
from django.contrib.sessions.models import Session
from django.test import Client
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import Therapist
from audit.models import AuditEntry
from core import tenancy
from core.models import Tenant

from .conftest import make_user, verified_client


@pytest.fixture
def owner(alpha):
    u = make_user(alpha, "chefin")
    u.role = Therapist.Role.OWNER
    u.save()
    return u


@pytest.fixture
def owner_client(owner):
    return verified_client(owner)


def test_only_owner_may_enter(client_beta, owner_client):
    assert client_beta.get("/verwaltung/").status_code == 403
    assert client_beta.get("/verwaltung/benutzer/").status_code == 403
    assert client_beta.post("/verwaltung/benutzer/neu/", {"name": "X", "username": "xxx"}).status_code == 403
    assert owner_client.get("/verwaltung/").status_code == 200
    assert owner_client.get("/verwaltung/benutzer/").status_code == 200


def test_nav_link_only_for_owner(client_beta, owner_client):
    assert "/verwaltung/" not in client_beta.get("/uebersicht/").content.decode()
    assert "/verwaltung/" in owner_client.get("/uebersicht/").content.decode()


def test_create_therapist_shows_password_once_and_forces_change(owner_client):
    r = owner_client.post("/verwaltung/benutzer/neu/", {"name": "Zeta Therapeut", "username": "zeta", "email": ""})
    assert r.status_code == 200
    html = r.content.decode()
    user = Therapist.objects.get(username="zeta")
    assert user.must_change_password and user.tenant.slug == "zeta" and not user.is_owner
    assert "Startpasswort" in html
    # Passwort steht nicht im Klartext in der Datenbank und wird nicht erneut angezeigt
    assert user.password.startswith(("pbkdf2", "md5", "argon"))
    assert Tenant.objects.get(slug="zeta").wrapped_key  # eigener Datenschlüssel


def test_duplicate_username_rejected(owner_client, user_beta):
    r = owner_client.post("/verwaltung/benutzer/neu/", {"name": "Dup", "username": "beta_user"})
    assert r.status_code == 200 and "schon vergeben" in r.content.decode()


def test_must_change_password_blocks_everything_else(user_beta):
    user_beta.must_change_password = True
    user_beta.save()
    client = verified_client(user_beta)
    r = client.get("/patients/")
    assert r.status_code == 302 and r["Location"].endswith("/passwort/")
    assert client.get("/passwort/").status_code == 200


def test_password_change_clears_flag(user_beta):
    user_beta.must_change_password = True
    user_beta.save()
    client = verified_client(user_beta)
    r = client.post("/passwort/", {"old_password": "ein-langes-passwort-123", "new_password1": "ganz-neues-Passwort-77x",
                                   "new_password2": "ganz-neues-Passwort-77x"})
    assert r.status_code == 302
    user_beta.refresh_from_db()
    assert not user_beta.must_change_password


def test_reset_password_ends_sessions_and_clears_lock(owner_client, user_beta):
    victim = verified_client(user_beta)
    user_beta.failed_logins, user_beta.locked_until = 5, None
    user_beta.save()
    old_hash = user_beta.password
    r = owner_client.post(f"/verwaltung/benutzer/{user_beta.pk}/passwort/")
    assert r.status_code == 200 and "Startpasswort" in r.content.decode()
    user_beta.refresh_from_db()
    assert user_beta.password != old_hash and user_beta.must_change_password and user_beta.failed_logins == 0
    assert victim.get("/patients/").status_code == 302  # Sitzung beendet


def test_reset_2fa_needs_identity_confirmation(owner_client, user_beta):
    verified_client(user_beta)
    url = f"/verwaltung/benutzer/{user_beta.pk}/zweiter-faktor/"
    owner_client.post(url)
    assert TOTPDevice.objects.filter(user=user_beta).exists()
    owner_client.post(url, {"identity_checked": "1"})
    assert not TOTPDevice.objects.filter(user=user_beta).exists()
    with tenancy.tenant_context(user_beta.tenant, tenancy.Actor(user_id=None, label="test")):
        assert AuditEntry.objects.using("t_beta").filter(action="auth.2fa_reset").exists()


def test_lock_and_unlock(owner_client, user_beta):
    victim = verified_client(user_beta)
    owner_client.post(f"/verwaltung/benutzer/{user_beta.pk}/sperren/", {"lock": "1"})
    user_beta.refresh_from_db()
    assert not user_beta.is_active
    assert victim.get("/patients/").status_code == 302
    owner_client.post(f"/verwaltung/benutzer/{user_beta.pk}/sperren/", {"lock": "0"})
    user_beta.refresh_from_db()
    assert user_beta.is_active


def test_owner_cannot_manage_own_account(owner_client, owner):
    owner_client.post(f"/verwaltung/benutzer/{owner.pk}/sperren/", {"lock": "1"})
    owner.refresh_from_db()
    assert owner.is_active
    owner_client.post(f"/verwaltung/benutzer/{owner.pk}/passwort/")
    assert Therapist.objects.get(pk=owner.pk).password == owner.password


def test_offboard_requires_both_confirmations(owner_client, user_beta, beta):
    url = f"/verwaltung/benutzer/{user_beta.pk}/ausscheiden/"
    owner_client.post(url, {"export_confirmed": "1", "confirm_name": "falsch"})
    beta.refresh_from_db()
    assert beta.is_active
    owner_client.post(url, {"confirm_name": "beta_user"})
    beta.refresh_from_db()
    assert beta.is_active
    owner_client.post(url, {"export_confirmed": "1", "confirm_name": "beta_user"})
    beta.refresh_from_db()
    assert not beta.is_active and beta.retain_until
    # ausgeschiedene Konten sind nicht mehr verwaltbar
    r = owner_client.post(f"/verwaltung/benutzer/{user_beta.pk}/passwort/")
    assert r.status_code == 302


def test_actions_are_post_only(owner_client, user_beta):
    for part in ("passwort", "zweiter-faktor", "sperren", "ausscheiden"):
        assert owner_client.get(f"/verwaltung/benutzer/{user_beta.pk}/{part}/").status_code == 405
    assert owner_client.get("/verwaltung/protokoll-pruefen/").status_code == 405


def test_audit_check_reports_ok(owner_client, user_beta):
    r = owner_client.post("/verwaltung/protokoll-pruefen/", follow=True)
    assert "Protokollketten" in r.content.decode()


def test_setup_checklist_progresses(owner_client):
    html = owner_client.get("/verwaltung/").content.decode()
    assert "Räume anlegen" in html and "von 6 erledigt" in html


def test_anonymous_redirected():
    assert Client().get("/verwaltung/").status_code == 302
