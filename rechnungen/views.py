from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import log as audit_log
from core import logo
from patients.models import Patient
from patients.views import tenant_required

from . import mailconfig, mailing, services
from .forms import LeistungForm, MahnForm, MailServerForm, PositionForm, ProfilForm, ZahlungForm
from .models import Honorarnote, Leistung, Mahnung, Versand, Position, Rechnungsprofil, eur
from .pdf import render_pdf, render_reminder_pdf


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
    form = ProfilForm(request.POST or None, request.FILES or None, instance=services.get_profile())
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        from einstellungen.views import refresh_own_logo_theme

        refresh_own_logo_theme(request.user, saved)
        messages.success(request, "Rechnungsprofil gespeichert.")
        return redirect("profile_edit")
    return render(request, "rechnungen/profile.html", {"form": form, "has_logo": bool(form.instance.logo_b64)})


@tenant_required
def reminder_settings(request):
    profile = services.get_profile()
    if profile is None:
        messages.info(request, "Bitte zuerst das Rechnungsprofil ausfüllen.")
        return redirect("profile_edit")
    form = MahnForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit_log("mahnwesen.einstellungen", profile)
        messages.success(request, "Mahnwesen gespeichert. Es gilt für neu erstellte Schreiben.")
        return redirect("reminder_settings")
    return render(request, "rechnungen/reminder_settings.html", {"form": form, "defaults": services.DEFAULT_REMINDER_TEXTS})


@tenant_required
def mail_server(request):
    profile = services.get_profile()
    if profile is None:
        messages.info(request, "Bitte zuerst das Rechnungsprofil ausfüllen.")
        return redirect("profile_edit")
    if request.method == "POST" and request.POST.get("action") == "test":
        to = profile.smtp_from or profile.email
        if not to:
            messages.error(request, "Für die Test-E-Mail fehlt eine Adresse (Absenderadresse oder E-Mail im Rechnungsprofil).")
        else:
            try:
                mailconfig.send_test(profile, to)
                messages.success(request, "Test-E-Mail wurde an Ihre eigene Adresse gesendet. Bitte Posteingang prüfen.")
            except Exception:  # Zugangsdaten/Adresse nie in die Meldung
                messages.error(request, "Der Test ist fehlgeschlagen. Bitte Servername, Port, Benutzername und Passwort prüfen.")
        return redirect("mail_server")
    form = MailServerForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit_log("mailserver.einstellungen", profile)  # nur Ereignis, keine Werte
        messages.success(request, "Mailserver gespeichert. Mit „Test-E-Mail senden“ können Sie ihn prüfen.")
        return redirect("mail_server")
    return render(request, "rechnungen/mail_server.html", {"form": form, "custom": mailconfig.is_custom(profile), "has_password": bool(profile.smtp_password)})


@tenant_required
def profile_logo(request):
    profile = services.get_profile()
    return logo.image_response(profile.logo_b64 if profile else "")


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
        "mail_block": mailing.mail_block_reason(invoice.patient), "mails": invoice.mails.all()[:10],
    }
    if invoice.status == Honorarnote.Status.AUSGESTELLT and invoice.kind == Honorarnote.Kind.RECHNUNG and bool(invoice.due_date and invoice.due_date < context["today"] and invoice.open_cents > 0):
        level, title, blocked = services.reminder_state(invoice, context["today"])
        context["next_reminder"] = {"level": level, "title": title, "blocked": blocked} if level else None
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
    today = timezone.localdate()
    rows = []
    for inv in services.overdue(today):
        level, title, blocked = services.reminder_state(inv, today)
        rows.append({"mail_block": mailing.mail_block_reason(inv.patient), "i": inv, "days": (today - inv.due_date).days, "level": level, "title": title, "blocked": blocked,
                     "last": inv.reminders.order_by("-level").first()})
    return render(request, "rechnungen/overdue.html", {"rows": rows, "today": today})


@tenant_required
@require_POST
def reminder_add(request, pk):
    invoice = get_object_or_404(Honorarnote, pk=pk)
    reminder = _guard(request, services.record_reminder, invoice, ok="Schreiben erstellt. Es steht jetzt zum Ausdrucken oder Speichern bereit.")
    if reminder is not None:
        return redirect("reminder_pdf", pk=reminder.pk)
    if request.POST.get("next") == "overdue":
        return redirect("overdue_list")
    return redirect("invoice_detail", pk=pk)


@tenant_required
@require_POST
def invoice_mail(request, pk):
    invoice = get_object_or_404(Honorarnote.objects.select_related("patient"), pk=pk)
    try:
        mailing.send_invoice(invoice)
        messages.success(request, "Honorarnote wurde per E-Mail versendet.")
    except mailing.MailError as exc:
        messages.error(request, str(exc))
    return redirect("invoice_detail", pk=pk)


@tenant_required
@require_POST
def reminder_mail(request, pk):
    reminder = get_object_or_404(Mahnung.objects.select_related("invoice__patient"), pk=pk)
    try:
        mailing.send_reminder(reminder)
        messages.success(request, "Schreiben wurde per E-Mail versendet.")
    except mailing.MailError as exc:
        messages.error(request, str(exc))
    if request.POST.get("next") == "overdue":
        return redirect("overdue_list")
    return redirect("invoice_detail", pk=reminder.invoice_id)


@tenant_required
def reminder_pdf(request, pk):
    reminder = get_object_or_404(Mahnung.objects.select_related("invoice__patient"), pk=pk)
    audit_log("mahnung.pdf", reminder.invoice)
    title = services.REMINDER_TITLES[reminder.level].replace(". ", "_").replace(" ", "_")
    response = HttpResponse(render_reminder_pdf(reminder), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{title}_{reminder.invoice.number}.pdf"'
    return response
