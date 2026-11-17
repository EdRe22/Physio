import json

from django.contrib import messages
from django.shortcuts import redirect, render

from audit.models import log as audit_log
from core import logo
from patients.views import tenant_required
from rechnungen import services as billing
from verwaltung import themes
from verwaltung.views import _gradient


def own_logo_color(request):
    """Hauptfarbe des eigenen Logos (Rechnungsprofil) oder None."""
    profile = billing.get_profile()
    return logo.dominant_color(profile.logo_b64) if profile and profile.logo_b64 else None


def refresh_own_logo_theme(user, profile):
    """Folgt die eigene Farbe dem Logo, wird sie bei Logowechsel neu berechnet; ohne verwertbares Logo gilt wieder die Praxisfarbe."""
    if user.theme_palette != themes.LOGO_SLUG:
        return
    color = logo.dominant_color(profile.logo_b64) if profile.logo_b64 else None
    if color:
        user.theme_custom = color
    else:
        user.theme_palette = user.theme_custom = ""
    user.save(update_fields=["theme_palette", "theme_custom"])


@tenant_required
def index(request):
    return redirect("profile_edit")


@tenant_required
def design(request):
    user = request.user
    logo_color = own_logo_color(request)
    if request.method == "POST":
        slug = request.POST.get("palette", "")
        custom = ""
        if slug == "":
            pass  # Praxisstandard
        elif slug == themes.LOGO_SLUG:
            custom = logo_color or ""
            if not custom:
                messages.error(request, "Aus Ihrem Logo lässt sich keine Farbe ableiten. Bitte zuerst im Rechnungsprofil ein farbiges Logo hochladen.")
                return redirect("my_design")
        elif slug not in themes.BY_SLUG:
            messages.error(request, "Bitte eine der angebotenen Farben auswählen.")
            return redirect("my_design")
        user.theme_palette, user.theme_custom = slug, custom
        user.save(update_fields=["theme_palette", "theme_custom"])
        audit_log("einstellungen.farbe", object_type="theme", object_id=slug or "standard")
        messages.success(request, "Farbe gespeichert. Sie gilt nur für Ihr Konto." if slug else "Es gilt wieder die Standardfarbe der Praxis.")
        return redirect("my_design")
    chosen = user.theme_palette
    options = [{"p": p, "light": json.dumps(p.light), "dark": json.dumps(p.dark), "gradient": _gradient(p), "checked": p.slug == chosen}
               for p in themes.PALETTES]
    if logo_color:
        p = themes.derive_palette(logo_color)
        options.append({"p": p, "light": json.dumps(p.light), "dark": json.dumps(p.dark), "gradient": _gradient(p),
                        "checked": chosen == themes.LOGO_SLUG, "sample": logo_color})
    from praxis.models import Erscheinungsbild

    row = Erscheinungsbild.objects.first()
    default = themes.palette_for(row.palette, row.custom_color) if row else themes.BY_SLUG[themes.DEFAULT]
    return render(request, "einstellungen/design.html", {"default_light": json.dumps(default.light), "default_dark": json.dumps(default.dark),
                                                         "options": options, "logo_color": logo_color, "uses_default": not chosen,
                                                         "active": "design"})


@tenant_required
def account(request):
    return render(request, "einstellungen/account.html", {"active": "account"})
