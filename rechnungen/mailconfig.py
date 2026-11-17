"""Eigener Mailserver je Therapeut.

Jeder Therapeut kann einen eigenen SMTP-Zugang (eigene Absenderadresse) hinterlegen. Ohne eigenen Zugang gilt der zentrale
Zugang der Praxis (Umgebungsvariablen). Das Passwort liegt verschlüsselt in der Datenbank des Therapeuten und wird nie
wieder angezeigt.

Sicherheit (der Server baut die Verbindung auf, der Host ist Anwendereingabe):
- nur Port 587 (STARTTLS) oder 465 (SSL/TLS), Zertifikat wird geprüft, kein Klartext
- Hostname muss ein öffentlicher Name/öffentliche Adresse sein: Loopback, private, Link-Local- und reservierte Adressen
  werden abgelehnt, auch beim Versand erneut (Schutz vor DNS-Umlenkung). Nur der Entwicklungsmodus erlaubt lokale Adressen.
"""
import ipaddress
import re
import socket

from django.conf import settings
from django.core.mail import EmailMessage, get_connection

ALLOWED_PORTS = {587: "starttls", 465: "ssl"}
HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
TIMEOUT = 15


class MailConfigError(Exception):
    pass


def _resolve(host):
    return [info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]


def check_host(host):
    """Wirft MailConfigError, wenn der Host unzulässig oder nicht öffentlich erreichbar ist."""
    if not HOST_RE.match(host or ""):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise MailConfigError("Bitte einen gültigen Servernamen angeben, zum Beispiel smtp.ihr-anbieter.at.") from None
    try:
        addresses = [host] if _is_ip(host) else _resolve(host)
    except OSError:
        raise MailConfigError("Der Servername konnte nicht aufgelöst werden.") from None
    if not settings.DEV:
        for addr in addresses:
            if not ipaddress.ip_address(addr.split("%")[0]).is_global:
                raise MailConfigError("Der Mailserver muss aus dem Internet erreichbar sein (keine lokale Adresse).")


def _is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def is_custom(profile) -> bool:
    return bool(profile and profile.smtp_host)


def connection_for(profile):
    """(Verbindung oder None für den zentralen Zugang, Absenderadresse)."""
    if not is_custom(profile):
        return None, settings.DEFAULT_FROM_EMAIL
    check_host(profile.smtp_host)
    conn = get_connection(
        "django.core.mail.backends.smtp.EmailBackend", host=profile.smtp_host, port=profile.smtp_port,
        username=profile.smtp_user, password=profile.smtp_password,
        use_tls=profile.smtp_port == 587, use_ssl=profile.smtp_port == 465, timeout=TIMEOUT, fail_silently=False,
    )
    return conn, profile.smtp_from or settings.DEFAULT_FROM_EMAIL


def send_test(profile, to):
    conn, sender = connection_for(profile)
    EmailMessage(
        subject="Test: Mailversand Ihrer Praxis-App",
        body="Diese Test-E-Mail bestätigt, dass Ihre Mailserver-Einstellungen funktionieren. Es sind keine Patientendaten enthalten.",
        from_email=f"{profile.name} <{sender}>", to=[to], connection=conn,
    ).send(fail_silently=False)
