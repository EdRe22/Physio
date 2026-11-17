"""Interne Job-Schnittstelle für Cron.

Befehle, die Mandanten öffnen, brauchen den Master-Schlüssel, der nur im Arbeitsspeicher der entsperrten App
liegt. Cron ruft sie deshalb per HTTP an die laufende App (nur Loopback, Bearer-Token) und nicht als eigenen Prozess.

Absicherung (alles muss zutreffen):
- PHYSIO_JOB_TOKEN gesetzt und mindestens 32 Zeichen, Vergleich zeitkonstant
- Anfrage kommt vom Loopback und trägt KEINE Proxy-Header (nginx würde sonst fremde Anfragen als lokal erscheinen lassen;
  zusätzlich muss nginx `location /intern/ { deny all; }` setzen)
- nur POST, nur Jobs aus der Liste unten
"""
import hmac
import io
from datetime import date

from django.conf import settings
from django.core.management import call_command
from django.http import HttpResponse, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt

JOBS = {
    "verify_audit": lambda request: ("verify_audit", [], {}),
    "send_reminders": lambda request: ("send_reminders", [], {}),
    "send_auto_reminders": lambda request: ("send_auto_reminders", [], {}),
    "purge_imports": lambda request: ("purge_imports", [], {}),
    "issue_rent": lambda request: ("issue_rent", [request.GET.get("period") or date.today().strftime("%Y-%m")], {}),
}
PROXY_HEADERS = ("HTTP_X_FORWARDED_FOR", "HTTP_X_REAL_IP", "HTTP_FORWARDED", "HTTP_X_FORWARDED_HOST")


def _text(status, text):
    return HttpResponse(text + "\n", status=status, content_type="text/plain; charset=utf-8")


@csrf_exempt
def run_job(request, name):
    token = getattr(settings, "PHYSIO_JOB_TOKEN", "")
    if len(token) < 32:
        return _text(404, "nicht verfügbar")
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.META.get("REMOTE_ADDR") not in ("127.0.0.1", "::1") or any(h in request.META for h in PROXY_HEADERS):
        return _text(403, "nur lokal")
    supplied = request.META.get("HTTP_AUTHORIZATION", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        return _text(403, "verweigert")
    if name not in JOBS:
        return _text(404, "unbekannter Job")
    command, args, kwargs = JOBS[name](request)
    out = io.StringIO()
    try:
        call_command(command, *args, stdout=out, stderr=out, **kwargs)
    except Exception as exc:  # Ausgabe geht an Cron-Mail, keine Daten, nur Meldung
        return _text(500, f"{out.getvalue()}FEHLER: {exc}")
    return _text(200, out.getvalue() or "OK")
