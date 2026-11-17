from django import forms
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from audit.models import log as audit_log
from core import tenancy
from core.models import Tenant
from patients.models import Patient
from patients.views import tenant_required
from rechnungen.services import BillingError

from . import services
from .models import MIN_REASON, NotfallZugriff


class RequestForm(forms.Form):
    target = forms.ModelChoiceField(label="Betroffener Therapeut", queryset=Tenant.objects.none())
    reason = forms.CharField(label=f"Begründung (mindestens {MIN_REASON} Zeichen)", widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, own_tenant_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].queryset = Tenant.objects.filter(is_active=True, purged_on__isnull=True).exclude(pk=own_tenant_id)


@tenant_required
def index(request):
    form = RequestForm(request.POST or None, own_tenant_id=request.user.tenant_id)
    if request.method == "POST" and form.is_valid():
        try:
            services.request_access(request.user, form.cleaned_data["target"], form.cleaned_data["reason"])
            messages.success(request, "Antrag gestellt. Ein anderer Therapeut muss ihn freigeben.")
            return redirect("notfall_index")
        except BillingError as exc:
            messages.error(request, str(exc))
    grants = list(services.visible_to(request.user))
    return render(request, "notfall/index.html", {
        "form": form, "grants": grants, "pending": services.open_for_approval(request.user), "me": request.user,
        "states": {g.pk: g.state() for g in grants},
    })


def _act(request, pk, func, ok):
    grant = get_object_or_404(NotfallZugriff, pk=pk)
    try:
        func(request.user, grant)
        messages.success(request, ok)
    except BillingError as exc:
        messages.error(request, str(exc))
    return redirect("notfall_index")


@tenant_required
@require_POST
def approve(request, pk):
    # Nicht sichtbare Vorgänge liefern 404, damit niemand fremde Anträge erraten kann.
    grant = get_object_or_404(NotfallZugriff, pk=pk)
    if not services.can_approve(request.user, grant):
        raise Http404
    return _act(request, pk, services.approve, "Freigegeben für 24 Stunden (nur lesend).")


@tenant_required
@require_POST
def end(request, pk):
    get_object_or_404(services.visible_to(request.user), pk=pk)
    return _act(request, pk, services.end, "Notfallzugriff beendet.")


def _grant_or_404(request, pk):
    grant = services.usable_grant(request.user, pk)
    if grant is None:
        raise Http404
    return grant


@tenant_required
def patients(request, pk):
    grant = _grant_or_404(request, pk)
    actor = tenancy.Actor(user_id=request.user.pk, label=f"notfall:{request.user.get_username()}", ip=request.META.get("REMOTE_ADDR", ""))
    with tenancy.tenant_context(grant.target, actor):
        audit_log("notfall.list", object_type="notfall", object_id=str(grant.pk))
        rows = sorted(Patient.objects.filter(archived_at__isnull=True), key=lambda p: p.full_name.casefold())
        return render(request, "notfall/patients.html", {"grant": grant, "patients": rows})


@tenant_required
def patient(request, pk, patient_pk):
    grant = _grant_or_404(request, pk)
    actor = tenancy.Actor(user_id=request.user.pk, label=f"notfall:{request.user.get_username()}", ip=request.META.get("REMOTE_ADDR", ""))
    with tenancy.tenant_context(grant.target, actor):
        pat = get_object_or_404(Patient, pk=patient_pk)
        audit_log("notfall.read", pat)
        entries = [(e, e.current) for e in pat.entries.all()]
        return render(request, "notfall/patient.html", {"grant": grant, "p": pat, "referrals": pat.referrals.all(), "entries": entries})
