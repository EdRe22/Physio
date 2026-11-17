from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import log as audit_log
from core import tenancy

from django.db import transaction

from .forms import EintragForm, EintragTextForm, PatientForm, ZuweisungForm
from .models import Eintrag, Einwilligung, Patient


def tenant_required(view):
    """Anmeldung mit zweitem Faktor plus zugeordneter Mandant sind Pflicht."""

    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if tenancy.current() is None:
            return HttpResponseForbidden("Kein Mandant zugeordnet.")
        return view(request, *args, **kwargs)

    return wrapper


@tenant_required
def patient_list(request):
    query = request.GET.get("q", "").strip().casefold()
    show_archived = request.GET.get("archiviert") == "1"
    qs = Patient.objects.all() if show_archived else Patient.objects.filter(archived_at__isnull=True)
    # Suche nach dem Entschlüsseln (verschlüsselte Felder sind nicht in der DB durchsuchbar).
    patients = [p for p in qs if not query or query in p.full_name.casefold()]
    patients.sort(key=lambda p: p.full_name.casefold())
    audit_log("patient.list")
    return render(request, "patients/list.html", {"patients": patients, "q": request.GET.get("q", ""), "archived": show_archived})


@tenant_required
def patient_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    audit_log("patient.read", patient)
    entries = [(entry, entry.current) for entry in patient.entries.all()]
    active = {c.kind: c for c in patient.consents.filter(revoked_at__isnull=True)}
    consents = [(kind, label, active.get(kind)) for kind, label in Einwilligung.Kind.choices]
    return render(request, "patients/detail.html", {
        "patient": patient, "consents": consents, "referrals": patient.referrals.all(), "entries": entries,
    })


@tenant_required
def referral_create(request, patient_pk):
    patient = get_object_or_404(Patient, pk=patient_pk)
    form = ZuweisungForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        referral = form.save(commit=False)
        referral.patient = patient
        referral.save()
        messages.success(request, "Zuweisung gespeichert.")
        return redirect("patient_detail", pk=patient.pk)
    return render(request, "patients/form.html", {"form": form, "title": "Neue Zuweisung"})


@tenant_required
def entry_create(request, patient_pk):
    patient = get_object_or_404(Patient, pk=patient_pk)
    initial = {"date": timezone.localdate(), "kind": request.GET.get("art", Eintrag.Kind.BEHANDLUNG)}
    form = EintragForm(request.POST or None, patient=patient, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic(using=tenancy.require().alias):
            entry = Eintrag.objects.create(
                patient=patient, referral=data["referral"], kind=data["kind"], date=data["date"]
            )
            entry.add_version(data["text"], author_id=request.user.pk)
        messages.success(request, "Eintrag gespeichert.")
        return redirect("patient_detail", pk=patient.pk)
    return render(request, "patients/form.html", {"form": form, "title": "Neuer Eintrag"})


@tenant_required
def entry_edit(request, pk):
    """Änderung = neue Version. Frühere Versionen bleiben sichtbar und unverändert."""
    entry = get_object_or_404(Eintrag, pk=pk)
    current = entry.current
    form = EintragTextForm(request.POST or None, initial={"text": current.text})
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data["text"] != current.text:
            entry.add_version(form.cleaned_data["text"], author_id=request.user.pk)
            messages.success(request, "Neue Version gespeichert.")
        return redirect("patient_detail", pk=entry.patient_id)
    audit_log("eintrag.read", entry)
    return render(request, "patients/entry_edit.html", {
        "form": form, "entry": entry, "versions": list(entry.versions.order_by("-version")),
    })


@tenant_required
@require_POST
def consent_grant(request, pk, kind):
    patient = get_object_or_404(Patient, pk=pk)
    if kind not in Einwilligung.Kind.values:
        return HttpResponseForbidden("Unbekannte Einwilligung.")
    if not patient.has_consent(kind):
        Einwilligung.objects.create(patient=patient, kind=kind, granted_at=timezone.now())
        messages.success(request, "Einwilligung erfasst.")
    return redirect("patient_detail", pk=patient.pk)


@tenant_required
@require_POST
def consent_revoke(request, pk, kind):
    patient = get_object_or_404(Patient, pk=pk)
    for consent in patient.consents.filter(kind=kind, revoked_at__isnull=True):
        consent.revoked_at = timezone.now()
        consent.save(update_fields=["revoked_at"])
    messages.success(request, "Einwilligung widerrufen.")
    return redirect("patient_detail", pk=patient.pk)


@tenant_required
def patient_create(request):
    form = PatientForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        patient = form.save()
        messages.success(request, "Patient angelegt.")
        return redirect("patient_detail", pk=patient.pk)
    return render(request, "patients/form.html", {"form": form, "title": "Neuer Patient"})


@tenant_required
def patient_edit(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    form = PatientForm(request.POST or None, instance=patient)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Änderungen gespeichert.")
        return redirect("patient_detail", pk=patient.pk)
    return render(request, "patients/form.html", {"form": form, "title": "Patient bearbeiten"})


@tenant_required
@require_POST
def patient_archive(request, pk):
    """Sperrt den Patienten (keine Löschung, wegen gesetzlicher Aufbewahrung)."""
    patient = get_object_or_404(Patient, pk=pk)
    patient.archived_at = timezone.now()
    patient.save(update_fields=["archived_at", "updated_at"])
    messages.success(request, "Patient archiviert.")
    return redirect("patient_list")
