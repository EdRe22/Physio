import segno
from django.contrib.auth import logout
from django.contrib.auth.views import PasswordChangeView
from django.urls import reverse_lazy
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from audit.models import log as audit_log
from core import tenancy
from core.net import client_ip


def _audit_login(request, action):
    """Audit-Eintrag direkt nach 2FA, wenn der Mandantenkontext noch nicht von der Middleware gesetzt wurde."""
    user = request.user
    if not user.tenant_id:
        return
    actor = tenancy.Actor(user.pk, user.get_username(), client_ip(request))
    with tenancy.tenant_context(user.tenant, actor):
        audit_log(action)


@login_required
def otp_verify(request):
    user = request.user
    if user.is_verified():
        return redirect("patient_list")
    device = user.totpdevice_set.filter(confirmed=True).first()
    if device is None:
        return redirect("otp_setup")
    error = ""
    if request.method == "POST":
        token = request.POST.get("token", "").strip().replace(" ", "")
        if device.verify_token(token):
            otp_login(request, device)
            request.session.cycle_key()  # neue Session-ID nach Erhöhung der Vertrauensstufe
            _audit_login(request, "auth.login")
            return redirect("patient_list")
        error = "Code ungültig oder zu viele Versuche. Bitte kurz warten und erneut versuchen."
    return render(request, "accounts/otp_verify.html", {"error": error})


@login_required
def otp_setup(request):
    user = request.user
    if user.totpdevice_set.filter(confirmed=True).exists():
        # Wer bereits einen zweiten Faktor hat, darf keinen weiteren nur mit Passwort einrichten.
        return redirect("otp_verify" if not user.is_verified() else "patient_list")
    device, _ = TOTPDevice.objects.get_or_create(user=user, confirmed=False, defaults={"name": "default"})
    error = ""
    if request.method == "POST":
        token = request.POST.get("token", "").strip().replace(" ", "")
        if device.verify_token(token):
            device.confirmed = True
            device.save()
            otp_login(request, device)
            request.session.cycle_key()
            _audit_login(request, "auth.2fa_enabled")
            return redirect("patient_list")
        error = "Code ungültig. Bitte erneut versuchen."
    qr = mark_safe(segno.make(device.config_url).svg_inline(scale=5, dark="#000", light="#fff"))
    from base64 import b32encode

    secret = b32encode(device.bin_key).decode()
    return render(request, "accounts/otp_setup.html", {"qr": qr, "secret": secret, "error": error})


class PasswordChange(PasswordChangeView):
    """Passwortwechsel (nur mit bestätigtem zweitem Faktor erreichbar, Regeln aus AUTH_PASSWORD_VALIDATORS)."""

    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("patient_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        _audit_login(self.request, "auth.password_changed")
        return response


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")
