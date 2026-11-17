import json
from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts import admin_ops
from accounts.models import Therapist
from audit.models import log as audit_log
from core import logo, provision, tenancy
from core.net import client_ip
from praxis import offboarding
from praxis.models import Erscheinungsbild
from praxis.views import owner_required

from . import services, themes
from .forms import NewTherapistForm


def _actor(request):
    return {"actor_label": request.user.get_username(), "actor_id": request.user.pk, "ip": client_ip(request)}


def _credentials(request, user, password, *, created):
    """Zeigt das Startpasswort genau einmal an. Es wird nirgends gespeichert und steht nicht in der Adresszeile."""
    return render(request, "verwaltung/credentials.html", {"u": user, "password": password, "created": created})


@owner_required
def index(request):
    steps = services.setup_steps(request.user)
    return render(request, "verwaltung/index.html", {
        "steps": steps, "done": sum(1 for s in steps if s.done), "total": len(steps), "system": services.system_status(),
    })


@owner_required
def users(request):
    rows = []
    for u in Therapist.objects.select_related("tenant").order_by("tenant__name", "username"):
        rows.append({
            "u": u, "has_2fa": TOTPDevice.objects.filter(user=u, confirmed=True).exists(),
            "state": ("ausgeschieden" if u.tenant and u.tenant.offboarded_on else "gesperrt" if not u.is_active else "aktiv"),
        })
    return render(request, "verwaltung/users.html", {"rows": rows})


@owner_required
def user_new(request):
    form = NewTherapistForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            tenant = provision.create_tenant(services.slug_from_username(d["username"]), d["name"])
        except provision.ProvisionError as exc:
            form.add_error(None, str(exc))
        else:
            password = admin_ops.generate_password()
            user = Therapist.objects.create_user(
                username=d["username"], email=d["email"], password=password, tenant=tenant,
                role=Therapist.Role.THERAPIST, must_change_password=True,
            )
            audit_log("admin.user_create", object_type="user", object_id=str(user.pk))
            return _credentials(request, user, password, created=True)
    return render(request, "verwaltung/user_new.html", {"form": form})


@owner_required
def user_detail(request, pk):
    u = get_object_or_404(Therapist.objects.select_related("tenant"), pk=pk)
    return render(request, "verwaltung/user_detail.html", {
        "u": u, "is_me": u.pk == request.user.pk, "has_2fa": TOTPDevice.objects.filter(user=u, confirmed=True).exists(),
        "offboarded": bool(u.tenant and u.tenant.offboarded_on), "today": date.today(),
    })


def _target(request, pk):
    """Ziel einer Verwaltungsaktion: nie das eigene Konto, nie ein ausgeschiedener Mandant."""
    u = get_object_or_404(Therapist.objects.select_related("tenant"), pk=pk)
    if u.pk == request.user.pk:
        messages.error(request, "Das eigene Konto lässt sich hier nicht verwalten. Das Passwort ändern Sie über „Passwort“ in der Seitenleiste.")
        return None
    if u.tenant and (u.tenant.offboarded_on or u.tenant.purged_on):
        messages.error(request, "Dieses Konto ist ausgeschieden und kann nicht mehr verwaltet werden.")
        return None
    return u


@owner_required
@require_POST
def user_password(request, pk):
    u = _target(request, pk)
    if u is None:
        return redirect("verwaltung_users")
    password = admin_ops.reset_password(u, **_actor(request))
    audit_log("admin.password_reset", object_type="user", object_id=str(u.pk))
    return _credentials(request, u, password, created=False)


@owner_required
@require_POST
def user_2fa(request, pk):
    u = _target(request, pk)
    if u is None:
        return redirect("verwaltung_users")
    if request.POST.get("identity_checked") != "1":
        messages.error(request, "Bitte bestätigen, dass die Identität der Person persönlich geprüft wurde.")
        return redirect("verwaltung_user", pk=pk)
    admin_ops.reset_2fa(u, **_actor(request))
    audit_log("admin.2fa_reset", object_type="user", object_id=str(u.pk))
    messages.success(request, f"Zweiter Faktor von {u.get_username()} zurückgesetzt. Beim nächsten Login wird ein neuer eingerichtet, "
                              "am besten gemeinsam mit der Person.")
    return redirect("verwaltung_user", pk=pk)


@owner_required
@require_POST
def user_lock(request, pk):
    u = _target(request, pk)
    if u is None:
        return redirect("verwaltung_users")
    lock = request.POST.get("lock") == "1"
    u.is_active = not lock
    u.failed_logins, u.locked_until = 0, None
    u.save(update_fields=["is_active", "failed_logins", "locked_until"])
    if lock:
        admin_ops.end_sessions(u)
    action = "admin.user_lock" if lock else "admin.user_unlock"
    audit_log(action, object_type="user", object_id=str(u.pk))
    admin_ops.log_in_target_chain(u, "auth.locked_by_owner" if lock else "auth.unlocked_by_owner", **_actor(request))
    messages.success(request, f"{u.get_username()} wurde {'gesperrt' if lock else 'wieder freigegeben'}.")
    return redirect("verwaltung_user", pk=pk)


@owner_required
@require_POST
def user_offboard(request, pk):
    u = _target(request, pk)
    if u is None or u.tenant is None:
        return redirect("verwaltung_users")
    if request.POST.get("confirm_name", "").strip() != u.get_username():
        messages.error(request, "Zur Bestätigung den Benutzernamen genau eintippen.")
        return redirect("verwaltung_user", pk=pk)
    try:
        end = offboarding.offboard(u.tenant, date.today(), request.POST.get("export_confirmed") == "1")
    except offboarding.OffboardingError as exc:
        messages.error(request, str(exc))
        return redirect("verwaltung_user", pk=pk)
    admin_ops.end_sessions(u)
    audit_log("admin.user_offboard", object_type="user", object_id=str(u.pk))
    messages.success(request, f"{u.tenant.name} ist ausgeschieden. Die Daten bleiben bis {end:%d.%m.%Y} verschlüsselt erhalten. "
                              "Die endgültige Löschung danach erledigt die technische Betreuung.")
    return redirect("verwaltung_users")


@owner_required
@require_POST
def audit_check(request):
    actor = tenancy.Actor(user_id=request.user.pk, label=request.user.get_username(), ip=client_ip(request))
    checked, bad = services.verify_all_audit_chains(actor)
    if bad:
        messages.error(request, "Achtung, die Protokollkette ist nicht in Ordnung bei: " + ", ".join(bad) + ". Bitte sofort die technische Betreuung informieren.")
    else:
        messages.success(request, f"Alle {checked} Protokollketten sind unverändert und lückenlos.")
    return redirect("verwaltung_index")


def _gradient(p):
    return f"linear-gradient(135deg,{p.light['side-from']},{p.light['brand']} 60%,{p.light['brand-2']})"


def _logo_color():
    """Hauptfarbe des Praxislogos (oder None, wenn kein Logo oder keine eindeutige Farbe)."""
    from praxis.models import PraxisProfil

    profile = PraxisProfil.objects.first()
    return logo.dominant_color(profile.logo_b64) if profile and profile.logo_b64 else None


@owner_required
def design(request):
    current = Erscheinungsbild.objects.first()
    chosen = current.palette if current else themes.DEFAULT
    logo_color = _logo_color()
    if request.method == "POST":
        slug = request.POST.get("palette", "")
        custom = ""
        if slug == themes.LOGO_SLUG:
            custom = logo_color or ""
            if not custom:
                messages.error(request, "Aus dem Praxislogo lässt sich keine Farbe ableiten. Bitte zuerst im Praxisprofil ein farbiges Logo hochladen.")
                return redirect("verwaltung_design")
        elif slug not in themes.BY_SLUG:
            messages.error(request, "Bitte eine der angebotenen Farben auswählen.")
            return redirect("verwaltung_design")
        obj = current or Erscheinungsbild()
        obj.palette, obj.custom_color = slug, custom
        obj.save()
        audit_log("admin.theme_change", object_type="theme", object_id=slug)
        name = themes.palette_for(slug, custom).name
        messages.success(request, f"Farbe „{name}“ gespeichert. Sie gilt jetzt als Standard für alle, die keine eigene Farbe gewählt haben.")
        return redirect("verwaltung_design")
    options = [{"p": p, "light": json.dumps(p.light), "dark": json.dumps(p.dark), "gradient": _gradient(p),
                "checked": p.slug == chosen} for p in themes.PALETTES]
    if logo_color:
        p = themes.derive_palette(logo_color)
        options.append({"p": p, "light": json.dumps(p.light), "dark": json.dumps(p.dark), "gradient": _gradient(p),
                        "checked": chosen == themes.LOGO_SLUG, "sample": logo_color})
    return render(request, "verwaltung/design.html", {"options": options, "logo_color": logo_color})
