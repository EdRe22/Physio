from functools import wraps

from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import log as audit_log
from core import logo
from patients.views import tenant_required
from rechnungen.services import BillingError

from . import services
from .forms import LeaseForm, PaidForm, PeriodForm, ProfilForm
from .models import Mietabrechnung, Mietverhaeltnis, PraxisProfil
from .pdf import render_pdf


def owner_required(view):
    @tenant_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_owner:
            return HttpResponseForbidden("Nur für den Praxis-Eigentümer.")
        return view(request, *args, **kwargs)

    return wrapper


def _invoice(request, pk):
    return get_object_or_404(services.visible_invoices(request.user), pk=pk)


def _run(request, func, *args, ok=None):
    try:
        result = func(*args)
    except BillingError as exc:
        messages.error(request, str(exc))
        return None
    if ok:
        messages.success(request, ok)
    return result


@tenant_required
def rent_list(request):
    invoices = list(services.visible_invoices(request.user))
    return render(request, "praxis/list.html", {
        "invoices": invoices, "owner": request.user.is_owner, "period_form": PeriodForm(initial={"period": timezone.localdate().strftime("%Y-%m")}),
        "profile": services.profile(),
    })


def _refresh_logo_theme(profile):
    """Ist die Praxisfarbe aus dem Logo abgeleitet, folgt sie einem neuen Logo. Ohne verwertbares Logo fällt sie auf Standard zurück."""
    from .models import Erscheinungsbild

    theme = Erscheinungsbild.objects.filter(palette="logo").first()
    if theme:
        color = logo.dominant_color(profile.logo_b64)
        if color:
            theme.custom_color = color
        else:
            theme.palette, theme.custom_color = "teal", ""
        theme.save()


@owner_required
def profile_edit(request):
    form = ProfilForm(request.POST or None, request.FILES or None, instance=services.profile())
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        _refresh_logo_theme(saved)
        messages.success(request, "Praxisprofil gespeichert.")
        return redirect("rent_list")
    return render(request, "praxis/profile.html", {"form": form, "has_logo": bool(form.instance.logo_b64)})


@owner_required
def profile_logo(request):
    profile = services.profile()
    return logo.image_response(profile.logo_b64 if profile else "")


@owner_required
def lease_list(request):
    form = LeaseForm(request.POST or None, owner_tenant_id=request.user.tenant_id)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        Mietverhaeltnis.objects.create(tenant=d["tenant"], rent_cents=d["rent"], utilities_cents=d["utilities"],
                                       valid_from=d["valid_from"], valid_until=d["valid_until"], note=d["note"])
        audit_log("miete.lease", object_type="lease", object_id=str(d["tenant"].pk))
        messages.success(request, "Mietverhältnis gespeichert.")
        return redirect("lease_list")
    return render(request, "praxis/leases.html", {"form": form, "leases": Mietverhaeltnis.objects.select_related("tenant")})


@owner_required
@require_POST
def issue_month(request):
    form = PeriodForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Bitte den Monat als JJJJ-MM angeben.")
        return redirect("rent_list")
    created, skipped = services.issue_all(form.cleaned_data["period"])
    messages.success(request, f"{len(created)} Mietabrechnung(en) ausgestellt.")
    for name, reason in skipped:
        messages.error(request, f"{name}: {reason}")
    return redirect("rent_list")


@tenant_required
def rent_detail(request, pk):
    invoice = _invoice(request, pk)
    audit_log("miete.read", invoice)
    return render(request, "praxis/detail.html", {
        "i": invoice, "owner": request.user.is_owner,
        "own": invoice.tenant_id == request.user.tenant_id, "paid_form": PaidForm(initial={"date": timezone.localdate()}),
    })


@tenant_required
def rent_pdf(request, pk):
    invoice = _invoice(request, pk)
    audit_log("miete.pdf", invoice)
    response = HttpResponse(render_pdf(invoice), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="Mietabrechnung-{invoice.number}.pdf"'
    return response


@owner_required
@require_POST
def rent_paid(request, pk):
    invoice = _invoice(request, pk)
    form = PaidForm(request.POST)
    if form.is_valid():
        _run(request, services.mark_paid, invoice, form.cleaned_data["date"], ok="Als bezahlt markiert und als Einnahme gebucht.")
    else:
        messages.error(request, "Bitte ein gültiges Zahlungsdatum angeben.")
    return redirect("rent_detail", pk=pk)


@tenant_required
@require_POST
def rent_book(request, pk):
    invoice = _invoice(request, pk)
    _run(request, services.book_as_expense, invoice, ok="Als Ausgabe (Miete) in Ihre Buchhaltung übernommen.")
    return redirect("rent_detail", pk=pk)


@owner_required
@require_POST
def rent_storno(request, pk):
    invoice = _invoice(request, pk)
    _run(request, services.storno, invoice, ok="Mietabrechnung storniert.")
    return redirect("rent_detail", pk=pk)
