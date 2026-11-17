from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import log as audit_log
from core import tenancy

from .forms import PatientForm
from .models import Patient


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
    return render(request, "patients/detail.html", {"patient": patient, "consents": patient.consents.all()})


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
