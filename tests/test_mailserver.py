import pytest
from django.core import mail

from rechnungen import mailconfig, mailing
from rechnungen.models import Rechnungsprofil

from .test_mailversand import issued
from .test_rechnungen import profile

GOOD = {"smtp_host": "smtp.mailanbieter.at", "smtp_port": 587, "smtp_user": "praxis@alpha.at",
        "smtp_password_new": "geheim-123", "smtp_from": "praxis@alpha.at"}


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(mailconfig, "_resolve", lambda host: ["93.184.216.34"])
    monkeypatch.setattr("django.conf.settings.DEV", False, raising=False)


def test_save_encrypts_password_and_never_shows_it(client_alpha, in_alpha):
    profile()
    assert client_alpha.post("/rechnungen/mailserver/", GOOD).status_code == 302
    p = Rechnungsprofil.objects.get()
    assert p.smtp_host == "smtp.mailanbieter.at" and p.smtp_password == "geheim-123"
    page = client_alpha.get("/rechnungen/mailserver/").content.decode()
    assert "geheim-123" not in page and "smtp.mailanbieter.at" in page
    from django.db import connections
    with connections["t_alpha"].cursor() as cur:
        cur.execute("select smtp_password from rechnungen_rechnungsprofil")
        assert "geheim-123" not in str(cur.fetchone()[0])


def test_blank_password_keeps_old_and_remove_clears(client_alpha, in_alpha):
    profile()
    client_alpha.post("/rechnungen/mailserver/", GOOD)
    client_alpha.post("/rechnungen/mailserver/", {**GOOD, "smtp_password_new": "", "smtp_user": "neu@alpha.at"})
    p = Rechnungsprofil.objects.get()
    assert p.smtp_password == "geheim-123" and p.smtp_user == "neu@alpha.at"
    client_alpha.post("/rechnungen/mailserver/", {"remove": "on"})
    p = Rechnungsprofil.objects.get()
    assert p.smtp_host == "" and not p.smtp_password and p.smtp_from == ""


@pytest.mark.parametrize("change", [
    {"smtp_port": 25}, {"smtp_port": 2525}, {"smtp_host": "localhost"}, {"smtp_host": "127.0.0.1"},
    {"smtp_host": "10.0.0.5"}, {"smtp_host": "192.168.1.10"}, {"smtp_host": "169.254.169.254"},
    {"smtp_host": "evil host"}, {"smtp_from": "a b@x.at"}, {"smtp_from": "x@y.at\r\nBcc: z@z.at"}, {"smtp_user": ""},
])
def test_invalid_or_internal_settings_rejected(client_alpha, in_alpha, change):
    profile()
    assert client_alpha.post("/rechnungen/mailserver/", {**GOOD, **change}).status_code == 200
    assert Rechnungsprofil.objects.get().smtp_host == ""


def test_hostname_resolving_to_private_address_is_rejected(client_alpha, in_alpha, monkeypatch):
    profile()
    monkeypatch.setattr(mailconfig, "_resolve", lambda host: ["10.1.2.3"])
    assert client_alpha.post("/rechnungen/mailserver/", GOOD).status_code == 200
    assert Rechnungsprofil.objects.get().smtp_host == ""


def test_password_required_for_new_server(client_alpha, in_alpha):
    profile()
    assert client_alpha.post("/rechnungen/mailserver/", {**GOOD, "smtp_password_new": ""}).status_code == 200


def test_send_uses_own_server_and_sender(in_alpha, monkeypatch):
    invoice = issued(smtp_host="smtp.mailanbieter.at", smtp_port=465, smtp_user="u", smtp_password="p", smtp_from="eigene@alpha.at")
    seen = {}
    real = mailconfig.get_connection

    def spy(backend, **kw):
        seen.update(kw)
        return real("django.core.mail.backends.locmem.EmailBackend")

    monkeypatch.setattr(mailconfig, "get_connection", spy)
    mailing.send_invoice(invoice)
    assert seen["host"] == "smtp.mailanbieter.at" and seen["use_ssl"] is True and seen["use_tls"] is False
    assert seen["username"] == "u" and seen["password"] == "p"
    assert "eigene@alpha.at" in mail.outbox[0].from_email


def test_private_host_blocked_at_send_time_and_logged(in_alpha, monkeypatch):
    invoice = issued(smtp_host="smtp.mailanbieter.at", smtp_user="u", smtp_password="p", smtp_from="a@alpha.at")
    monkeypatch.setattr(mailconfig, "_resolve", lambda host: ["127.0.0.1"])  # DNS-Umlenkung nach dem Speichern
    with pytest.raises(mailing.MailError, match="fehlgeschlagen"):
        mailing.send_invoice(invoice)
    assert mail.outbox == []


def test_default_central_sender_without_own_server(in_alpha):
    invoice = issued()
    mailing.send_invoice(invoice)
    assert "example.invalid" in mail.outbox[0].from_email


def test_test_mail_button(client_alpha, in_alpha, monkeypatch):
    profile()
    monkeypatch.setattr(mailconfig, "get_connection", lambda *a, **k: __import__("django.core.mail", fromlist=["x"]).get_connection("django.core.mail.backends.locmem.EmailBackend"))
    client_alpha.post("/rechnungen/mailserver/", GOOD)
    r = client_alpha.post("/rechnungen/mailserver/", {"action": "test"}, follow=True)
    assert mail.outbox and mail.outbox[-1].to == ["praxis@alpha.at"] and "Test-E-Mail wurde" in r.content.decode()


def test_other_therapist_has_own_settings(client_alpha, client_beta, in_alpha):
    profile()
    client_alpha.post("/rechnungen/mailserver/", GOOD)
    body = client_beta.get("/rechnungen/mailserver/", follow=True).content.decode()
    assert "smtp.mailanbieter.at" not in body
