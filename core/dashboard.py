"""Startseite „Übersicht“: was heute ansteht. Zeigt nur Daten des angemeldeten Therapeuten (Mandantenkontext)."""
from datetime import datetime, time, timedelta

from django.shortcuts import render
from django.utils import timezone

from audit.models import log as audit_log
from buchhaltung.models import BankTransaktion
from patients.models import Zuweisung
from patients.views import tenant_required
from raeume.models import Raumbelegung
from rechnungen import services as billing
from rechnungen.models import Honorarnote, eur
from termine.models import Termin


def _greeting(hour: int) -> str:
    if hour < 11:
        return "Guten Morgen"
    if hour < 18:
        return "Guten Tag"
    return "Guten Abend"


@tenant_required
def uebersicht(request):
    now = timezone.localtime()
    today = now.date()
    lo = timezone.make_aware(datetime.combine(today, time.min))
    hi = lo + timedelta(days=1)

    termine = list(Termin.objects.filter(start__gte=lo, start__lt=hi).select_related("patient").order_by("start"))
    upcoming = [t for t in termine if t.status == Termin.Status.GEPLANT and t.end >= now]
    invoices = Honorarnote.objects.filter(kind=Honorarnote.Kind.RECHNUNG, status=Honorarnote.Status.AUSGESTELLT)
    open_total = sum(i.open_cents for i in invoices)
    overdue = billing.overdue()
    drafts = Honorarnote.objects.filter(status=Honorarnote.Status.ENTWURF).count()
    open_tx = BankTransaktion.objects.filter(status=BankTransaktion.Status.OFFEN).count()

    low = []
    for z in Zuweisung.objects.select_related("patient"):
        ending = z.valid_until and (z.valid_until - today).days <= 14
        if z.units_left <= 2 or ending:
            low.append(z)
    rooms_today = [
        b for b in Raumbelegung.objects.filter(tenant_id=request.user.tenant_id, start__gte=lo, start__lt=hi, cancelled_at__isnull=True)
        .select_related("room").order_by("start")
    ]

    audit_log("uebersicht.view")
    return render(request, "dashboard.html", {
        "greeting": _greeting(now.hour), "today": today,
        "termine": termine, "next_termin": upcoming[0] if upcoming else None, "done_count": sum(1 for t in termine if t.status == Termin.Status.ERLEDIGT),
        "open_total": eur(open_total), "open_count": invoices.count(), "overdue_count": len(overdue), "draft_count": drafts,
        "open_tx": open_tx, "low_referrals": low[:6], "rooms_today": rooms_today,
    })
