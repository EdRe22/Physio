from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import log as audit_log
from patients.views import tenant_required
from rechnungen.models import Honorarnote

from . import services
from .forms import BuchungForm, ZuordnungForm
from .models import BankTransaktion, Beleg, Buchung, Kategorie


def _year(request):
    try:
        return int(request.GET.get("jahr", ""))
    except ValueError:
        return timezone.localdate().year


def _run(request, func, *args, ok=None, **kwargs):
    try:
        result = func(*args, **kwargs)
    except services.billing.BillingError as exc:
        messages.error(request, str(exc))
        return None
    if ok:
        messages.success(request, ok)
    return result


@tenant_required
def journal(request):
    services.ensure_categories()
    year = _year(request)
    buchungen = Buchung.objects.filter(date__year=year).select_related("category")
    kind = request.GET.get("art", "")
    if kind in Kategorie.Kind.values:
        buchungen = buchungen.filter(kind=kind)
    return render(request, "buchhaltung/journal.html", {
        "buchungen": list(buchungen), "year": year, "kind": kind,
        "open_tx": BankTransaktion.objects.filter(status=BankTransaktion.Status.OFFEN).count(),
    })


@tenant_required
def buchung_new(request):
    services.ensure_categories()
    form = BuchungForm(request.POST or None, request.FILES or None, initial={"date": timezone.localdate(), "kind": "ausgabe"})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            beleg = services.store_beleg(d["beleg"]) if d["beleg"] else None
            buchung = services.create_buchung(d["date"], d["kind"], d["category"], d["amount"], d["text"], d["counterpart"])
            if beleg:
                services.attach_beleg(beleg, buchung)
        except services.billing.BillingError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Buchung {buchung.number} gespeichert.")
            return redirect("buchung_detail", pk=buchung.pk)
    return render(request, "buchhaltung/buchung_form.html", {"form": form})


@tenant_required
def buchung_detail(request, pk):
    buchung = get_object_or_404(Buchung.objects.select_related("category"), pk=pk)
    audit_log("buchung.read", buchung)
    return render(request, "buchhaltung/buchung_detail.html", {
        "b": buchung, "belege": buchung.belege.all(),
        "reversible": not buchung.reverses_id and not hasattr(buchung, "reversed_by"),
    })


@tenant_required
@require_POST
def buchung_reverse(request, pk):
    buchung = get_object_or_404(Buchung, pk=pk)
    storno = _run(request, services.reverse, buchung, ok="Stornobuchung erstellt.")
    return redirect("buchung_detail", pk=storno.pk if storno else pk)


@tenant_required
@require_POST
def beleg_attach(request, pk):
    buchung = get_object_or_404(Buchung, pk=pk)
    upload = request.FILES.get("beleg")
    if not upload:
        messages.error(request, "Bitte eine Datei wählen.")
    else:
        try:
            services.attach_beleg(services.store_beleg(upload), buchung)
            messages.success(request, "Beleg angehängt.")
        except services.billing.BillingError as exc:
            messages.error(request, str(exc))
    return redirect("buchung_detail", pk=pk)


@tenant_required
def beleg_download(request, pk):
    beleg = get_object_or_404(Beleg, pk=pk)
    try:
        data = services.read_beleg(beleg)
    except FileNotFoundError:
        raise Http404("Belegdatei fehlt")
    except services.billing.BillingError as exc:
        return HttpResponse(str(exc), status=409)
    audit_log("beleg.read", beleg)
    ext = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}[beleg.content_type]
    response = HttpResponse(data, content_type=beleg.content_type)
    response["Content-Disposition"] = f'attachment; filename="{beleg.number}.{ext}"'
    return response


@tenant_required
def bank_list(request):
    services.ensure_categories()
    status = request.GET.get("status", BankTransaktion.Status.OFFEN)
    transactions = list(BankTransaktion.objects.filter(status=status)[:300]) if status in BankTransaktion.Status.values else []
    invoices = services.open_invoices()
    rows = []
    for tx in transactions:
        match = services.suggest(tx, invoices)
        rows.append({
            "tx": tx, "match": match[0] if match else None, "confidence": match[1] if match else "",
            "categories": Kategorie.objects.filter(active=True, kind="einnahme" if tx.amount_cents > 0 else "ausgabe"),
        })
    return render(request, "buchhaltung/bank.html", {
        "rows": rows, "status": status, "statuses": BankTransaktion.Status.choices,
        "sure_count": sum(1 for r in rows if r["confidence"] == "sicher"),
    })


@tenant_required
@require_POST
def bank_accept(request, pk):
    tx = get_object_or_404(BankTransaktion, pk=pk)
    invoice = get_object_or_404(Honorarnote, pk=request.POST.get("invoice") or 0)
    _run(request, services.accept_match, tx, invoice, ok="Zahlung zugeordnet und als Einnahme gebucht.")
    return redirect("bank_list")


@tenant_required
@require_POST
def bank_accept_all(request):
    count = services.accept_all_sure()
    messages.success(request, f"{count} sichere Zuordnungen übernommen.")
    return redirect("bank_list")


@tenant_required
@require_POST
def bank_book(request, pk):
    tx = get_object_or_404(BankTransaktion, pk=pk)
    form = ZuordnungForm(request.POST, kind="einnahme" if tx.amount_cents > 0 else "ausgabe")
    if form.is_valid():
        _run(request, services.book_transaction, tx, form.cleaned_data["category"], form.cleaned_data["text"], ok="Umsatz gebucht.")
    else:
        messages.error(request, "Bitte eine passende Kategorie wählen.")
    return redirect("bank_list")


@tenant_required
@require_POST
def bank_ignore(request, pk):
    tx = get_object_or_404(BankTransaktion, pk=pk)
    _run(request, services.ignore_transaction, tx, ok="Umsatz ignoriert (nicht betrieblich).")
    return redirect("bank_list")


@tenant_required
def report(request):
    year = _year(request)
    return render(request, "buchhaltung/report.html", {"r": services.year_report(year), "year": year})


@tenant_required
def export(request):
    year = _year(request)
    if request.GET.get("format") == "zip":
        response = HttpResponse(services.export_zip(year), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="buchhaltung_{year}.zip"'
        return response
    audit_log("export.buchhaltung", object_type="export", object_id=str(year))
    response = HttpResponse(services.journal_csv(year), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="journal_{year}.csv"'
    return response
