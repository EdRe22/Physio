"""Demo-Konten (siehe `create_demo`) und die ausdrücklich einschaltbare Ausnahme vom 2FA-Zwang.

Die Ausnahme gilt nur, wenn alles zutrifft: `PHYSIO_DEMO_OHNE_2FA=1`, das Konto ist eines der Demo-Konten (Benutzername
UND erfundene Adresse) und es hat keinen bestätigten zweiten Faktor. Wer freiwillig einen einrichtet, muss ihn
danach auch benutzen. Alle anderen Konten brauchen weiterhin 2FA.
"""
from django.conf import settings

DEMO_USERNAMES = ("maria.muster", "peter.beispiel", "sophie.probst", "tom.testmann")


def demo_email(username: str) -> str:
    return f"{username}@example.invalid"


def second_factor_waived(user) -> bool:
    if not settings.PHYSIO_DEMO_OHNE_2FA:
        return False
    username = user.get_username()
    if username not in DEMO_USERNAMES or user.email != demo_email(username):
        return False
    return not user.totpdevice_set.filter(confirmed=True).exists()
