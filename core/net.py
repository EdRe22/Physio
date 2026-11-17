import ipaddress

from django.conf import settings

LOOPBACK = ("127.0.0.1", "::1")


def client_ip(request) -> str:
    """IP des Clients fürs Audit-Log. Hinter dem Reverse-Proxy (nginx auf Loopback) steht die echte Adresse im
    letzten Eintrag von X-Forwarded-For (vom Proxy angehängt, nicht fälschbar). Der Header wird NUR beachtet, wenn
    PHYSIO_TRUST_PROXY=1 gesetzt ist und die Verbindung vom Loopback kommt."""
    addr = request.META.get("REMOTE_ADDR", "")
    if settings.PHYSIO_TRUST_PROXY and addr in LOOPBACK:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            try:
                return str(ipaddress.ip_address(forwarded.split(",")[-1].strip()))
            except ValueError:
                return addr
    return addr
