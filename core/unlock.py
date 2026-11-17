from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_protect

from . import keystore


@csrf_protect
def unlock_view(request):
    """Entsperrseite. Existiert nur, wenn eine Schlüsseldatei konfiguriert ist."""
    if not settings.PHYSIO_KEYFILE:
        raise Http404
    if keystore.is_unlocked():
        return redirect("/")
    error = ""
    if request.method == "POST":
        upload = request.FILES.get("keyfile")
        try:
            keystore.unlock(settings.PHYSIO_KEYFILE, request.POST.get("passphrase", ""), upload.read(4096) if upload else b"")
            return redirect("/")
        except keystore.KeyUnlockError as exc:
            error = str(exc)
    return render(request, "unlock.html", {"error": error}, status=200 if not error else 403)
