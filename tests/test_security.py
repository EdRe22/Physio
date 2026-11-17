import re

import pytest
from django.test import Client, RequestFactory, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from django_otp.plugins.otp_totp.models import TOTPDevice

from audit.models import AuditEntry, verify_chain
from core import tenancy
from core.net import client_ip
from tests.conftest import totp_token, verified_client

NEW_PASSWORD = "ein-ganz-anderes-passwort-987"


# --- IP hinter dem Reverse-Proxy ---

def req(remote, forwarded=None):
    extra = {"REMOTE_ADDR": remote}
    if forwarded is not None:
        extra["HTTP_X_FORWARDED_FOR"] = forwarded
    return RequestFactory().get("/", **extra)


def test_forwarded_header_ignored_unless_proxy_is_trusted():
    assert client_ip(req("127.0.0.1", "203.0.113.7")) == "127.0.0.1"  # PHYSIO_TRUST_PROXY aus


@override_settings(PHYSIO_TRUST_PROXY=True)
def test_forwarded_header_trusted_only_from_loopback_and_uses_last_hop():
    assert client_ip(req("127.0.0.1", "203.0.113.7")) == "203.0.113.7"
    assert client_ip(req("127.0.0.1", "6.6.6.6, 203.0.113.7")) == "203.0.113.7"  # vorangestellte Fälschung wird ignoriert
    assert client_ip(req("198.51.100.9", "6.6.6.6")) == "198.51.100.9"  # nicht vom Proxy: Header zählt nicht
    assert client_ip(req("127.0.0.1", "kein-ip")) == "127.0.0.1"
    assert client_ip(req("127.0.0.1")) == "127.0.0.1"


@override_settings(PHYSIO_TRUST_PROXY=True)
def test_audit_log_records_real_client_ip(client_alpha, alpha):
    client_alpha.get("/patients/", HTTP_X_FORWARDED_FOR="203.0.113.7", REMOTE_ADDR="127.0.0.1")
    with tenancy.tenant_context(alpha):
        assert AuditEntry.objects.get(action="patient.list").ip == "203.0.113.7"


# --- Passwortwechsel ---

def test_password_change_flow(client_alpha, user_alpha, alpha):
    page = client_alpha.get("/passwort/")
    assert page.status_code == 200
    base = {"old_password": "ein-langes-passwort-123"}
    for bad in ({"old_password": "falsch", "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
                {**base, "new_password1": "kurz", "new_password2": "kurz"},
                {**base, "new_password1": "123456789012", "new_password2": "123456789012"},
                {**base, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD + "x"}):
        assert client_alpha.post("/passwort/", bad).status_code == 200
    user_alpha.refresh_from_db()
    assert user_alpha.check_password("ein-langes-passwort-123")  # nichts geändert
    ok = client_alpha.post("/passwort/", {**base, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
    assert ok.status_code == 302
    user_alpha.refresh_from_db()
    assert user_alpha.check_password(NEW_PASSWORD)
    assert client_alpha.get("/patients/").status_code == 200  # Sitzung bleibt gültig
    with tenancy.tenant_context(alpha):
        assert AuditEntry.objects.filter(action="auth.password_changed").count() == 1 and verify_chain()[0]


def test_password_change_requires_login_and_second_factor(user_alpha):
    assert Client().get("/passwort/").status_code == 302
    only_password = Client()
    only_password.force_login(user_alpha)
    r = only_password.get("/passwort/")
    assert r.status_code == 302 and "2fa" in r["Location"]  # ohne zweiten Faktor nicht erreichbar


# --- Sitzung ---

def test_session_id_rotates_after_second_factor(user_alpha):
    device = TOTPDevice.objects.create(user=user_alpha, name="default", confirmed=True)
    client = Client()
    client.force_login(user_alpha)
    before = client.session.session_key
    r = client.post("/2fa/", {"token": totp_token(device)})
    assert r.status_code == 302
    assert client.session.session_key != before
    assert client.get("/patients/").status_code == 200


# --- Eingabegrenzen ---

def test_absurd_year_parameter_does_not_crash(client_alpha):
    for value in ("99999999999999999999", "-5", "abc", "0"):
        assert client_alpha.get(f"/buchhaltung/?jahr={value}").status_code == 200
        assert client_alpha.get(f"/buchhaltung/auswertung/?jahr={value}").status_code == 200


# --- Rundumtest: jede Route verlangt Anmeldung ---

PUBLIC = {
    "/login/": {200},  # Anmeldeseite
    "/logout/": {405},  # nur POST
    "/entsperren/": {404},  # ohne Schlüsseldatei nicht vorhanden
}
JOB_PREFIX = "/intern/job/"
STATIC_PREFIX = "/static/"  # öffentliche Oberflächen-Dateien (Skript), enthalten keine Daten


def _routes(patterns=None, prefix=""):
    for p in patterns if patterns is not None else get_resolver().url_patterns:
        route = prefix + str(p.pattern)
        if isinstance(p, URLResolver):
            yield from _routes(p.url_patterns, route)
        elif isinstance(p, URLPattern):
            yield route


def _concrete(route):
    route = re.sub(r"<int:[a-z_]+>", "1", route)
    route = re.sub(r"<str:[a-z_]+>", "x", route)
    route = re.sub(r"<[a-z_]+:[a-z_]+>", "1", route)
    return "/" + route.lstrip("^").rstrip("$")


def test_every_route_rejects_anonymous_users():
    routes = sorted({_concrete(r) for r in _routes()})
    assert len(routes) > 60  # Schutz davor, dass die Routenermittlung stillschweigend leer läuft
    for path in routes:
        for method in ("get", "post"):
            r = getattr(Client(), method)(path)
            if path == "/":  # Startseite leitet nur auf die (geschützte) Patientenliste um
                assert r.status_code == 302 and r["Location"] == "/uebersicht/"
                assert Client().get("/uebersicht/").status_code == 302
            elif path in PUBLIC:
                assert r.status_code in PUBLIC[path] | {302, 405, 403}, (method, path, r.status_code)
            elif path.startswith(STATIC_PREFIX):
                assert r.status_code in (200, 404, 405)
            elif path.startswith(JOB_PREFIX):
                assert r.status_code in (403, 404, 405), (method, path, r.status_code)  # ohne Token nie ausführbar
            else:
                assert r.status_code == 302 and "/login/" in r["Location"], (method, path, r.status_code)


def test_every_route_requires_second_factor(user_alpha):
    only_password = Client()
    only_password.force_login(user_alpha)
    allowed = {"/login/", "/logout/", "/2fa/", "/2fa/setup/", "/entsperren/"}
    for path in sorted({_concrete(r) for r in _routes()}):
        if path in allowed or path.startswith((JOB_PREFIX, STATIC_PREFIX)):
            continue
        for method in ("get", "post"):
            r = getattr(only_password, method)(path)
            assert r.status_code == 302 and ("/2fa" in r["Location"]), (method, path, r.status_code, r.get("Location"))


# --- Rundumtest: Mandantentrennung über alle Objekt-URLs ---

def _seed_alpha(alpha):
    """Daten in Alphas Mandant, die es bei Beta nicht gibt (ID 1..n)."""
    from datetime import date, timedelta

    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.utils import timezone

    from buchhaltung import services as books
    from buchhaltung.models import BankTransaktion
    from patients.models import Eintrag, Einwilligung, Patient, Zuweisung
    from rechnungen import services as billing
    from rechnungen.models import Honorarnote, Leistung, Rechnungsprofil
    from termine.models import Termin

    with tenancy.tenant_context(alpha):
        Rechnungsprofil.objects.create(name="Alpha", address="A 1\n1010 Wien", iban="AT611904300234573201",
                                       exemption_text="Fake", exemption_confirmed_on=date.today())
        for _ in range(2):
            p = Patient.objects.create(first_name="Geheim", last_name="Patient", address="Weg 1\n1010 Wien")
            Einwilligung.objects.create(patient=p, kind="email_reminder", granted_at=timezone.now())
            ref = Zuweisung.objects.create(patient=p, doctor="Dr. X", units_total=3, valid_from=date.today())
            e = Eintrag.objects.create(patient=p, referral=ref, kind="behandlung", date=date.today() - timedelta(days=1))
            e.add_version("vertraulich")
            Termin.objects.create(patient=p, start=timezone.now() + timedelta(days=1))
            inv = Honorarnote.objects.create(patient=p)
            billing.add_position(inv, date.today(), "Therapie", 1, 5500)
            billing.add_payment(billing.issue(inv), date.today(), 1000)
            Honorarnote.objects.create(patient=p)  # Entwurf
        Leistung.objects.create(name="Einzel", price_cents=5500)
        books.create_buchung(date.today(), "ausgabe", books.category("Miete", "ausgabe"), 1000, "x")
        beleg = books.store_beleg(SimpleUploadedFile("a.pdf", b"%PDF-1.4 x"))
        books.attach_beleg(beleg, books.Buchung.objects.get(source="manuell"))
        BankTransaktion.objects.create(date=date.today(), amount_cents=-100, tx_hash="seed")

        def counts():
            from audit.models import AuditEntry
            from importer.models import ImportJob
            from rechnungen.models import Mahnung, Zahlung

            models = (Patient, Einwilligung, Zuweisung, Eintrag, Termin, Honorarnote, Zahlung, Mahnung, Leistung,
                      books.Buchung, books.Beleg, BankTransaktion, ImportJob)
            return {m.__name__: m.objects.count() for m in models}

        return counts()


def test_other_tenant_can_neither_read_nor_change_any_object_route(alpha, beta, client_beta, client_alpha):
    seeded = _seed_alpha(alpha)
    # Gegenprobe: dieselben Adressen funktionieren für den Eigentümer der Daten (sonst wäre der Test wertlos)
    for path in ("/patients/1/", "/patients/1/export/", "/rechnungen/1/", "/rechnungen/1/pdf/", "/buchhaltung/buchung/1/", "/buchhaltung/beleg/1/"):
        assert client_alpha.get(path).status_code == 200, path
    object_routes = sorted({_concrete(r) for r in _routes() if "<int:" in r})
    assert len(object_routes) > 30
    checked = 0
    for route in object_routes:
        for number in (1, 2, 3):
            path = re.sub(r"(?<=/)1(?=/|$)", str(number), route)
            for method in ("get", "post"):
                r = getattr(client_beta, method)(path)
                # Erlaubt: nicht gefunden, nicht erlaubt, verboten oder Umleitung. Nie Inhalt und nie ein Serverfehler.
                assert r.status_code in (302, 403, 404, 405), (method, path, r.status_code)
                if r.status_code == 302:  # Umleitungen dürfen nichts über fremde Objekte verraten
                    assert "geheim" not in r["Location"].lower()
                checked += 1
    assert checked > 150
    with tenancy.tenant_context(alpha):
        from audit.models import AuditEntry  # noqa: F401
        from importer.models import ImportJob
        from patients.models import Eintrag, Einwilligung, Patient, Zuweisung
        from rechnungen.models import Honorarnote, Leistung, Mahnung, Zahlung
        from termine.models import Termin
        from buchhaltung.models import BankTransaktion, Beleg, Buchung

        after = {m.__name__: m.objects.count() for m in (Patient, Einwilligung, Zuweisung, Eintrag, Termin, Honorarnote, Zahlung,
                                                         Mahnung, Leistung, Buchung, Beleg, BankTransaktion, ImportJob)}
        assert after == seeded  # nichts verändert, nichts angelegt, nichts gelöscht
        assert Patient.objects.filter(archived_at__isnull=False).count() == 0
        assert Honorarnote.objects.filter(status="storniert").count() == 0
    with tenancy.tenant_context(beta):
        assert Patient.objects.count() == 0 and Honorarnote.objects.count() == 0 and Buchung.objects.count() == 0


# --- Zweiten Faktor zurücksetzen ---

def test_reset_2fa_removes_device_ends_sessions_and_logs(alpha, user_alpha, user_beta, capsys):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    client = verified_client(user_alpha)
    other = verified_client(user_beta)
    assert client.get("/patients/").status_code == 200
    call_command("reset_2fa", "alpha_user")
    assert not TOTPDevice.objects.filter(user=user_alpha).exists()
    assert client.get("/patients/").status_code == 302  # alte Sitzung ist beendet
    assert other.get("/patients/").status_code == 200  # andere Benutzer bleiben unberührt
    fresh = Client()
    assert fresh.login(username="alpha_user", password="ein-langes-passwort-123")
    assert "setup" in fresh.get("/patients/")["Location"]  # muss neuen Faktor einrichten
    with tenancy.tenant_context(alpha):
        assert AuditEntry.objects.filter(action="auth.2fa_reset").count() == 1 and verify_chain()[0]
    with pytest.raises(CommandError, match="nicht gefunden"):
        call_command("reset_2fa", "gibtesnicht")


def test_static_files_are_limited_to_the_static_folder():
    c = Client()
    assert c.get("/static/ui.js").status_code == 200
    for bad in ("/static/../manage.py", "/static/..%2fmanage.py", "/static/%2e%2e/config/settings.py"):
        assert c.get(bad).status_code in (301, 400, 403, 404), bad
