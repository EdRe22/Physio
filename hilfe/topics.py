"""Verzeichnis der Hilfethemen. Der Inhalt steht in templates/hilfe/topics/<slug>.html.

Zuordnung `AREAS`: Wer in einem Bereich der App auf „Hilfe“ klickt, landet direkt beim passenden Thema.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    slug: str
    title: str
    summary: str
    owner_only: bool = False


TOPICS = [
    Topic("erste-schritte", "Erste Schritte", "Anmelden, zweiter Faktor, Passwort, automatische Abmeldung"),
    Topic("patienten", "Patienten", "Anlegen, suchen, archivieren, Einwilligungen, Datenkopie"),
    Topic("dokumentation", "Dokumentation und Zuweisungen", "Einträge, Versionen, Einheiten einer Zuweisung"),
    Topic("termine", "Termine und Erinnerungen", "Kalender, Serien, Status, E-Mail-Erinnerung"),
    Topic("raeume", "Räume", "Raumplan, Reservierung, Termin mit Raum, freie Räume"),
    Topic("honorarnoten", "Honorarnoten", "Erstellen, ausstellen, stornieren, Zahlungen, Mahnstufen"),
    Topic("buchhaltung", "Buchhaltung", "Journal, Belege, Auswertung, Export für den Steuerberater"),
    Topic("bankabgleich", "Kontoauszug und Zahlungsabgleich", "Bankumsätze importieren und Honorarnoten zuordnen"),
    Topic("import", "Daten importieren (Excel/CSV)", "Bestandsdaten übernehmen, Prüfung vor dem Import"),
    Topic("miete", "Meine Miete", "Mietabrechnungen ansehen und in die Buchhaltung übernehmen"),
    Topic("notfall", "Notfallzugriff (Vertretung)", "Wenn ein Kollege ausfällt: beantragen, freigeben, Regeln"),
    Topic("datenschutz", "Datenschutz und Protokoll", "Wer sieht was, was wird protokolliert, Datenpanne"),
    Topic("probleme", "Probleme und Fragen", "Häufige Fehlermeldungen und was zu tun ist"),
    Topic("eigentuemer-verwaltung", "Verwaltung: Konten und Einrichtung (Eigentümer)", "Therapeut anlegen, Passwort und zweiten Faktor zurücksetzen, sperren", True),
    Topic("eigentuemer-praxis", "Praxis einrichten (Eigentümer)", "Räume, Mietverhältnisse, Mietabrechnung, Steuerhinweis", True),
    Topic("eigentuemer-ausscheiden", "Ausscheiden und Praxisende (Eigentümer)", "Ablauf, Fristen, endgültige Löschung", True),
    Topic("eigentuemer-technik", "Betrieb für den Eigentümer", "Neustart und Entsperren, Sicherung, Zugang zurücksetzen", True),
]
BY_SLUG = {t.slug: t for t in TOPICS}

# Pfadanfang -> Thema (längere Präfixe zuerst prüfen)
AREAS = [
    ("/patients/", "patienten"), ("/termine/", "termine"), ("/raeume/", "raeume"), ("/rechnungen/", "honorarnoten"),
    ("/buchhaltung/bank/", "bankabgleich"), ("/buchhaltung/", "buchhaltung"), ("/import/", "import"),
    ("/verwaltung/", "eigentuemer-verwaltung"), ("/praxis/", "miete"), ("/notfall/", "notfall"), ("/passwort/", "erste-schritte"), ("/2fa/", "erste-schritte"),
]


def topic_for_path(path: str) -> str | None:
    for prefix, slug in AREAS:
        if path.startswith(prefix):
            return slug
    return None


def visible_topics(user):
    return [t for t in TOPICS if not t.owner_only or getattr(user, "is_owner", False)]
