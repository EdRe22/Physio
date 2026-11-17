from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import log as audit_log
from patients.models import Patient
from patients.views import tenant_required

from . import services
from .forms import LeistungForm, PositionForm, ProfilForm, ZahlungForm
from .models import Honorarnote, Leistung, Position, Rechnungsprofil, eur
from .pdf import render_pdf


def _guard(request, func, *args, ok=None, **kwargs):
    """Führt eine Service-Funktion aus und meldet fachliche Fehler als Hinweis."""
    try:
        result = func(*args, **kwargs)
    except services.BillingError as exc:
        messages.error(request, str(exc))
        return None
    if ok:
        messages.success(request, ok)
    return result


@tenant_required
def profile_edit(request):
    form = ProfilForm(request.POST or None, instance=services.get_profile())
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Rechnungsprofil gespeichert.")
        return redirect("invoice_list")
    return render(request, "patients/form.html", {"form": form, "title": "Rechnungsprofil"})


@tenant_required
def leistung_list(request):
    return render(request, "rechnungen/leistungen.html", {"leistungen": Leistung.objects.all()})


@tenant_required
def leistung_edit(request, pk=None):
    instance = get_object_or_404(Leistung, pk=pk) if pk else None
    form = LeistungForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Leistung gespeichert.")
        return redirect("leistung_list")
    return render(request, "patients/form.html", {"form": form, "title": "Leistung"})


@tenant_required
def invoice_list(request):
    status = request.GET.get("status", "")
    invoices = Honorarnote.objects.select_related("patient")
    if status in Honorarnote.Status.values:
        invoices = invoices.filter(status=status)
    invoices = list(invoices)
    open_total = sum(i.open_cents for i in invoices if i.status == Honorarnote.Status.AUSGESTELLT)
    profile = services.get_profile()
    return render(request, "rechnungen/list.html", {
        "invoices": invoices, "open_total": eur(open_total), "status": status, "statuses": Honorarnote.Status.choices,
        "profile_ready": bool(profile and profile.ready_to_issue),
    })


@tenant_required
def invoice_new(request, patient_pk):
    patient = get_object_or_404(Patient, pk=patient_pk)
    entries = list(services.unbilled_entries(patient).order_by("date", "id"))
    leistungen = Leistung.objects.filter(active=True)
    if request.method == "POST":
        chosen = [e for e in entries if str(e.pk) in request.POST.getlist("entries")]
        leistung = leistungen.filter(pk=request.POST.get("leistung") or 0).first()
        invoice = _guard(request, services.create_draft, patient, chosen, leistung)
        if invoice:
            return redirect("invoice_detail", pk=invoice.pk)
    return render(request, "rechnungen/new.html", {"patient": patient, "entries": entries, "leistungen": leistungen})


@tenant_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(Honorarnote.objects.select_related("patient"), pk=pk)
    audit_log("rechnung.read", invoice)
    context = {
        "invoice": invoice, "positions": invoice.positions.all(), "payments": invoice.payments.all(),
        "reminders": invoice.reminders.all(), "today": timezone.localdate(),
    }
    if invoice.is_draft:
        context["position_form"] = PositionForm(initial={"date": timezone.localdate()})
    elif invoice.status == Honorarnote.Status.AUSGESTELLT and invoice.kind == Honorarnote.Kind.RECHNUNG:
        context["payment_form"] = ZahlungForm(initial={"date": timezone.localdate(), "amount": f"{invoice.open_cents / 100:.2f}".replace(".", ",")})
    return render(request, "rechnungen/detail.html", context)


@tenant_required
@require_POST
def position_add(request, pk):
    invoice = get_object_or_404(Honorarnote, pk=pk)
    form = PositionForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        _guard(request, services.add_position, invoice, d["date"], d["final_description"], d["quantity"], d["final_price_cents"])
    else:
        for errors in form.errors.values():
            messages.error(request, " ".join(errors))
    return redirect("invoice_detail", pk=invoice.pk)


@tenant_required
@require_POST
def position_delete(request, pk, position_pk):
    position = get_object_or_404(Position, pk=position_pk, invoice_id=pk)
    _guard(request, services.remove_position, position)
    return redirect("invoice_detail", pk=pk)


@tenant_required
@require_POST
def invoice_issue(request, pk):
    invoice = get_object_or_404(Honorarnote, pk=pk)
    _guard(request, services.issue, invoice, ok="Honorarnote ausgestellt.")
    return redirect("invoice_detail", pk=pk)


@tenant_required
@require_POST
def invoice_delete(request, pk):
    invoice = get_object_or_404(Honorarnote, pk=pk)
    patient_id = invoice.patient_id
    try:
        services.delete_draft(invoice)
    except services.BillingError as exc:
        messages.error(request, str(exc))
        return redirect("invoice_detail", pk=pk)
    messages.success(request, "Entwurf gelöscht.")
    return redirect("patient_detail", pk=patient_id)


@tenant_required
@require_POST
def invoice_storno(request, pk):
    invoice = get_object_or_404(Honorarnote, pk=pk)
    storno = _guard(request, services.storno, invoice, ok="Stornorechnung erstellt.")
    return redirect("invoice_detail", pk=storno.pk if storno else pk)


@tenant_required
@require_POST
def payment_add(request, pk):
    invoice = get_object_or_404(Honorarnote, pk=pk)
    form = ZahlungForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        _guard(request, services.add_payment, invoice, d["date"], d["amount"], d["method"], ok="Zahlung erfasst.")
    else:
        for errors in form.errors.values():
            messages.error(request, " ".join(errors))
    return redirect("invoice_detail", pk=pk)


@tenant_required
def invoice_pdf(request, pk):
    invoice = get_object_or_404(Honorarnote.objects.select_related("patient"), pk=pk)
    audit_log("rechnung.pdf", invoice)
    name = f"Honorarnote_{invoice.number}" if invoice.number else f"Entwurf_{invoice.pk}"
    response = HttpResponse(render_pdf(invoice), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{name}.pdf"'
    return response


@tenant_required
def overdue_list(request):
    return render(request, "rechnungen/overdue.html", {"invoices": services.overdue(), "today": timezone.localdate()})


@tenant_required
@require_POST
def reminder_add(request, pk):
    invoice = get_object_or_404(Honorarnote, pk=pk)
    _guard(request, services.record_reminder, invoice, ok="Mahnstufe erfasst.")
    if request.POST.get("next") == "overdue":
        return redirect("overdue_list")
    return redirect("invoice_detail", pk=pk)
