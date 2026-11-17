from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse

from . import tenancy


class SecurityHeadersMiddleware:
    """Zusätzliche Header; Gesundheitsdaten dürfen nicht im Browser-Cache landen."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'self'",
        )
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers["Cache-Control"] = "no-store"
        return response


class TwoFactorRequiredMiddleware:
    """Ohne bestätigten zweiten Faktor ist außer Login, 2FA und Logout nichts erreichbar."""

    OPEN_NAMES = ("login", "logout", "otp_verify", "otp_setup")

    def __init__(self, get_response):
        self.get_response = get_response

    def _open_paths(self):
        return {reverse(name) for name in self.OPEN_NAMES}

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not user.is_verified():
            if request.path not in self._open_paths() and not request.path.startswith("/static/"):
                has_device = user.totpdevice_set.filter(confirmed=True).exists()
                return redirect("otp_verify" if has_device else "otp_setup")
        return self.get_response(request)


class TenantMiddleware:
    """Setzt den Mandanten des angemeldeten und verifizierten Therapeuten für den Request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if request.path == "/entsperren/":  # Entsperrseite braucht keinen Mandanten (und der Schlüssel ist ggf. noch gesperrt)
            return self.get_response(request)
        if user.is_authenticated and user.is_verified() and user.tenant_id:
            tenant = user.tenant
            if not tenant.is_active:
                return HttpResponseForbidden("Mandant deaktiviert.")
            actor = tenancy.Actor(user_id=user.pk, label=user.get_username(), ip=request.META.get("REMOTE_ADDR", ""))
            with tenancy.tenant_context(tenant, actor):
                return self.get_response(request)
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, tenancy.TenantNotSet):
            return HttpResponseForbidden("Kein Mandant zugeordnet.")
        return None


class LockedMiddleware:
    """Solange der Master-Schlüssel nicht entsperrt ist, ist nur die Entsperrseite erreichbar (503)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.http import HttpResponse

        from . import keystore

        if settings.PHYSIO_KEYFILE and not keystore.is_unlocked() and request.path != "/entsperren/":
            return HttpResponse("System gesperrt. Der Eigentümer muss es unter /entsperren/ freigeben.", status=503,
                                content_type="text/plain; charset=utf-8")
        return self.get_response(request)
