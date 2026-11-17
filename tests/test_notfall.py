from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from audit.models import AuditEntry, verify_chain
from core import tenancy
from core.models import Tenant
from notfall import services
from notfall.models import DURATION, NotfallZugriff
from patients.models import Eintrag, Patient
from tests.conftest import make_user, verified_client

REASON = "Therapeut Alpha ist nach einem Unfall im Krankenhaus und nicht erreichbar."


@pytest.fixture
def gamma(db):
    return make_user(Tenant.provision("gamma", "Therapeut Gamma"), "gamma_user")


@pytest.fixture
def delta(db):
    return make_user(Tenant.provision("delta", "Therapeut Delta"), "delta_user")


@pytest.fixture
def seeded(alpha):
    with tenancy.tenant_context(alpha):
        p = Patient.objects.create(first_name="Maria", last_name="Muster", phone="0660 999")
        e = Eintrag.objects.create(patient=p, kind="befund", date="2026-01-05")
        e.add_version("Streng vertrauliche Dokumentation XYZ")
    return p


def grant_for(requester, target, approver=None):
    g = services.request_access(requester, target, REASON)
    if approver:
        services.approve(approver, g)
    return NotfallZugriff.objects.get(pk=g.pk)


# --- Antrag ---

def test_request_rules(alpha, beta, user_alpha, user_beta):
    with pytest.raises(services.EmergencyError, match="eigenen"):
        services.request_access(user_beta, beta, REASON)
    with pytest.raises(services.EmergencyError, match="Begründung"):
        services.request_access(user_beta, alpha, "zu kurz")
    g = services.request_access(user_beta, alpha, REASON)
    assert g.state() == "beantragt"
    with pytest.raises(services.EmergencyError, match="offenen Antrag"):
        services.request_access(user_beta, alpha, REASON)
    alpha.is_active = False
    alpha.save()
    with pytest.raises(services.EmergencyError, match="nicht aktiv"):
        services.request_access(make_user(Tenant.provision("zeta", "Z"), "zeta_user"), alpha, REASON)


# --- Vier-Augen-Freigabe ---

def test_four_eyes_approval(alpha, beta, user_alpha, user_beta, gamma):
    g = grant_for(user_beta, alpha)
    for who in (user_beta, user_alpha):  # Antragsteller und Betroffener dürfen nicht freigeben
        with pytest.raises(services.EmergencyError, match="anderen Therapeuten"):
            services.approve(who, g)
    assert NotfallZugriff.objects.get(pk=g.pk).state() == "beantragt"
    services.approve(gamma, g)
    g = NotfallZugriff.objects.get(pk=g.pk)
    assert g.state() == "aktiv" and g.approved_by_id == gamma.pk and g.expires_at - g.approved_at == DURATION
    with pytest.raises(services.EmergencyError, match="nicht mehr offen"):
        services.approve(gamma, g)


def test_one_user_per_tenant_so_no_second_login_of_requesters_tenant(alpha, beta, user_beta):
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):  # sonst könnte ein zweiter Login desselben Therapeuten die Vier-Augen-Regel aushebeln
        make_user(beta, "beta_zweiter")


def test_grant_expires_after_24_hours_and_can_be_ended(alpha, beta, user_alpha, user_beta, gamma):
    g = grant_for(user_beta, alpha, gamma)
    now = timezone.now()
    assert g.is_active(now + timedelta(hours=23)) and g.state(now + timedelta(hours=25)) == "abgelaufen"
    assert services.usable_grant(user_beta, g.pk, now + timedelta(hours=25)) is None
    services.end(user_alpha, g)  # Betroffener darf jederzeit widerrufen
    assert NotfallZugriff.objects.get(pk=g.pk).state() == "beendet"
    assert services.usable_grant(user_beta, g.pk) is None
    with pytest.raises(services.EmergencyError, match="Bereits"):
        services.end(user_beta, g)


def test_stranger_cannot_end_and_only_requester_can_use(alpha, beta, user_alpha, user_beta, gamma, delta):
    g = grant_for(user_beta, alpha, gamma)
    with pytest.raises(services.EmergencyError, match="Berechtigung"):
        services.end(delta, g)
    assert services.usable_grant(gamma, g.pk) is None  # auch der Freigebende bekommt keinen Zugriff
    assert services.usable_grant(user_alpha, g.pk) is None
    assert services.usable_grant(user_beta, g.pk) is not None


# --- Protokoll beim Betroffenen ---

def test_every_step_is_logged_in_targets_chain(alpha, beta, user_alpha, user_beta, gamma):
    g = grant_for(user_beta, alpha, gamma)
    services.end(user_beta, g)
    with tenancy.tenant_context(alpha):
        entries = list(AuditEntry.objects.filter(object_type="notfall"))
        assert [e.action for e in entries] == ["notfall.request", "notfall.approve", "notfall.end"]
        assert [e.actor_label for e in entries] == ["notfall:beta_user", "notfall:gamma_user", "notfall:beta_user"]
        assert verify_chain()[0]
    with tenancy.tenant_context(beta):
        assert AuditEntry.objects.count() == 0  # nichts in fremden Ketten


def test_target_is_notified_by_email_without_patient_data(alpha, beta, user_alpha, user_beta, gamma):
    user_alpha.email = "alpha@example.invalid"
    user_alpha.save()
    g = grant_for(user_beta, alpha, gamma)
    assert len(mail.outbox) == 2 and all(m.to == ["alpha@example.invalid"] for m in mail.outbox)
    assert "freigegeben" in mail.outbox[1].body and "Muster" not in "".join(m.body for m in mail.outbox)


# --- HTTP ---

def test_http_flow_read_only_access_with_audit(alpha, beta, user_alpha, user_beta, gamma, seeded):
    cb, cg, ca = verified_client(user_beta), verified_client(gamma), verified_client(user_alpha)
    r = cb.post("/notfall/", {"target": alpha.pk, "reason": REASON})
    assert r.status_code == 302
    g = NotfallZugriff.objects.get()
    assert cb.get(f"/notfall/{g.pk}/patienten/").status_code == 404  # noch nicht freigegeben
    assert REASON[:30] in cg.get("/notfall/").content.decode()  # Freigeber sieht den Antrag
    assert cb.post(f"/notfall/{g.pk}/freigeben/").status_code == 404  # Antragsteller kann nicht selbst freigeben
    assert ca.post(f"/notfall/{g.pk}/freigeben/").status_code == 404  # Betroffener auch nicht
    assert cg.post(f"/notfall/{g.pk}/freigeben/").status_code == 302
    page = cb.get(f"/notfall/{g.pk}/patienten/")
    assert page.status_code == 200 and "Muster" in page.content.decode() and "Therapeut Alpha" in page.content.decode()
    detail = cb.get(f"/notfall/{g.pk}/patient/{seeded.pk}/").content.decode()
    assert "Streng vertrauliche Dokumentation XYZ" in detail
    assert cg.get(f"/notfall/{g.pk}/patienten/").status_code == 404  # Freigeber selbst hat keinen Datenzugriff
    with tenancy.tenant_context(alpha):
        actions = [e.action for e in AuditEntry.objects.filter(object_type__in=["notfall", "patients.patient"])]
        assert {"notfall.request", "notfall.approve", "notfall.list", "notfall.read"} <= set(actions) and verify_chain()[0]
    assert "beta_user" in ca.get("/notfall/").content.decode()  # Betroffener sieht den Zugriff (Transparenz)
    assert ca.post(f"/notfall/{g.pk}/beenden/").status_code == 302
    assert cb.get(f"/notfall/{g.pk}/patienten/").status_code == 404  # sofort gesperrt


def test_access_is_read_only_no_write_routes(alpha, beta, user_beta, user_alpha, gamma, seeded):
    g = grant_for(user_beta, alpha, gamma)
    cb = verified_client(user_beta)
    for suffix in ("patienten/", f"patient/{seeded.pk}/"):
        assert cb.post(f"/notfall/{g.pk}/{suffix}", {"x": "y"}).status_code in (200, 405)
    with tenancy.tenant_context(alpha):
        assert Patient.objects.count() == 1 and Eintrag.objects.get().current.text.startswith("Streng")


def test_foreign_patient_id_and_wrong_requester_are_404(alpha, beta, user_alpha, user_beta, gamma, delta, seeded):
    g = grant_for(user_beta, alpha, gamma)
    assert verified_client(user_beta).get(f"/notfall/{g.pk}/patient/99999/").status_code == 404
    assert verified_client(delta).get(f"/notfall/{g.pk}/patienten/").status_code == 404
    assert verified_client(delta).post(f"/notfall/{g.pk}/beenden/").status_code == 404  # fremde Vorgänge sind unsichtbar


def test_requires_login():
    from django.test import Client

    for path in ("/notfall/", "/notfall/1/patienten/", "/notfall/1/patient/1/"):
        assert Client().get(path).status_code == 302
