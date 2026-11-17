"""Demo-Praxis mit ausschließlich erfundenen Daten (für Vorführungen).

Legt eine Eigentümerin und drei weitere Therapeuten mit je eigener Datenbank an und füllt sie mit Fake-Patienten, Terminen,
Honorarnoten (bezahlt, offen, überfällig mit Mahnung), Räumen, Mietabrechnungen und Buchungen. Läuft nur in einer
LEEREN Installation (keine Mandanten) und nur mit ausdrücklicher Bestätigung. Echte Daten dürfen nie in eine Demo-Installation.

Die Passwörter werden zufällig erzeugt und GENAU EINMAL am Ende ausgegeben, nirgends gespeichert.
Der zweite Faktor wird beim ersten Login der Person eingerichtet (wie im Echtbetrieb).
"""
import secrets
from datetime import date, datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Therapist
from core import provision, tenancy
from core.management.commands._keys import require_key
from core.models import Tenant

IBANS = {"maria": "AT611904300234573201", "peter": "AT483200000012345864", "sophie": "AT022050302101023600", "tom": "AT342011122233344455"}

PEOPLE = [
    # slug, Anzeigename, Benutzername, Rolle, Palette
    ("maria", "Mag. Maria Muster", "maria.muster", Therapist.Role.OWNER, ""),
    ("peter", "Peter Beispiel, BSc", "peter.beispiel", Therapist.Role.THERAPIST, "blau"),
    ("sophie", "Sophie Probst", "sophie.probst", Therapist.Role.THERAPIST, "gruen"),
    ("tom", "Tom Testmann", "tom.testmann", Therapist.Role.THERAPIST, "bordeaux"),
]

PATIENTS = [
    ("Anna", "Berger", "1984-03-12", "Rückenschmerzen lumbal"), ("Karl", "Huber", "1957-11-02", "Schulter rechts, Impingement"),
    ("Lena", "Gruber", "1992-07-25", "Kniegelenk links nach VKB-Plastik"), ("Franz", "Wimmer", "1949-01-30", "Hüft-TEP rechts"),
    ("Julia", "Steiner", "1979-09-18", "Nackenverspannung, HWS-Syndrom"), ("Markus", "Pichler", "1968-05-06", "Tennisarm links"),
    ("Eva", "Maier", "2001-12-14", "Sprunggelenk nach Distorsion"), ("Georg", "Leitner", "1975-04-21", "Bandscheibenvorfall L5/S1"),
]
STREETS = ["Beispielgasse", "Musterweg", "Testplatz", "Demostraße", "Fakeallee"]
DOCTORS = ["Dr. Fiktiv, Allgemeinmedizin", "Dr. Muster, Orthopädie", "Dr. Test, Unfallchirurgie"]


class Command(BaseCommand):
    help = "Legt eine Demo-Praxis mit erfundenen Daten an (nur in leerer Installation)."

    def add_arguments(self, parser):
        parser.add_argument("--confirm-fake-data", action="store_true", required=True,
                            help="Bestätigt: Diese Installation ist eine Demo und enthält nie echte Daten.")
        parser.add_argument("--credentials-file", help="Zugangsdaten zusätzlich in diese Datei schreiben (nur Leserechte für den Benutzer). Sonst nur Ausgabe am Bildschirm.")

    def handle(self, *args, credentials_file=None, **opts):
        require_key()
        if Tenant.objects.exists() or Therapist.objects.exists():
            raise CommandError("Abbruch: Die Installation ist nicht leer. Die Demo wird nur in einer neuen Installation angelegt.")
        today = timezone.localdate()
        passwords, tenants = {}, {}
        for i, (slug, name, username, role, palette) in enumerate(PEOPLE):
            tenant = provision.create_tenant(slug, name)
            tenants[slug] = tenant
            passwords[username] = secrets.token_urlsafe(12)
            Therapist.objects.create_user(
                username=username, password=passwords[username], tenant=tenant, role=role, email=f"{username}@example.invalid",
                theme_palette=palette, theme_custom="",
            )
            with tenancy.tenant_context(tenant):
                self._fill_therapist(slug, name, today, i)
            self.stdout.write(f"  {name}: Daten angelegt")
        with tenancy.tenant_context(tenants["maria"]):  # Miete und Räume gehören zur Eigentümerin (Audit-Log)
            self._fill_practice(tenants, today)
        self.stdout.write(self.style.SUCCESS("\nDemo-Praxis angelegt. Zugangsdaten (werden nur jetzt angezeigt, bitte notieren):"))
        for username, pw in passwords.items():
            self.stdout.write(f"  {username:18} {pw}")
        self.stdout.write("Beim ersten Login richtet jede Person ihren zweiten Faktor (Authenticator-App) ein.")
        if credentials_file:
            import os

            fd = os.open(credentials_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("DEMO-Zugangsdaten (erfundene Daten). Nach der Vorführung löschen.\n")
                fh.writelines(f"{u}\t{pw}\n" for u, pw in passwords.items())
            self.stdout.write(f"Zugangsdaten gespeichert in {credentials_file} (nach der Vorführung löschen).")

    # --- je Therapeut ---
    def _fill_therapist(self, slug, name, today, idx):
        from buchhaltung import services as books
        from buchhaltung.models import Kategorie
        from patients.models import Einwilligung, Eintrag, Patient, Zuweisung
        from rechnungen import services
        from rechnungen.models import Honorarnote, Leistung, Rechnungsprofil
        from termine.models import Termin

        Rechnungsprofil.objects.create(
            name=name, title="Physiotherapeut/in", address=f"{STREETS[idx]} {idx + 3}\n1010 Wien", phone="+43 1 234 56 78",
            email=f"{slug}@example.invalid", iban=IBANS[slug], payment_days=14,
            exemption_text="Umsatzsteuerfreie Heilbehandlung. DEMO-TEXT, nicht vom Steuerberater geprüft.",
            exemption_confirmed_on=today, reminder_days=7,
        )
        for lname, cents in (("Physiotherapie Einzel (30 Min.)", 5500), ("Manuelle Therapie (45 Min.)", 7200),
                             ("Lymphdrainage (45 Min.)", 6800), ("Erstbefund und Beratung", 8500)):
            Leistung.objects.create(name=lname, price_cents=cents)

        patients = []
        for j, (first, last, born, diagnosis) in enumerate(PATIENTS[: 5 + idx % 3]):
            p = Patient.objects.create(
                first_name=first, last_name=last, birth_date=born, insurer="ÖGK (Demo)", insurance_no=f"{1000 + j * 37} {born[8:10]}{born[5:7]}{born[2:4]}",
                address=f"{STREETS[(j + idx) % 5]} {10 + j}\n{1010 + j * 10} Wien", phone=f"+43 660 000 {100 + j}{idx}",
                email=f"{first.lower()}.{last.lower()}@example.invalid",
            )
            patients.append(p)
            ref = Zuweisung.objects.create(
                patient=p, doctor=DOCTORS[j % 3], diagnosis=diagnosis, units_total=10,
                valid_from=today - timedelta(days=60), valid_until=today + timedelta(days=120),
            )
            first_day = today - timedelta(days=45 - j * 3)
            for kind, text, day in (
                (Eintrag.Kind.ANAMNESE, f"Erstgespräch (Demo). Beschwerden: {diagnosis}. Seit ca. 3 Wochen, belastungsabhängig.", first_day),
                (Eintrag.Kind.BEFUND, "Bewegungsausmaß leicht eingeschränkt, Druckschmerz lokal (erfundener Demo-Befund).", first_day),
                (Eintrag.Kind.BEHANDLUNG, "Mobilisation, Dehnung, Heimübungen erklärt (Demo).", first_day + timedelta(days=7)),
                (Eintrag.Kind.BEHANDLUNG, "Kräftigung, Haltungsschulung. Schmerz deutlich besser (Demo).", first_day + timedelta(days=14)),
            ):
                Eintrag.objects.create(patient=p, referral=ref, kind=kind, date=day).add_version(text)
            if j < 2:
                Einwilligung.objects.create(patient=p, kind=Einwilligung.Kind.EMAIL_DOCUMENTS, granted_at=timezone.now())
                Einwilligung.objects.create(patient=p, kind=Einwilligung.Kind.EMAIL_REMINDER, granted_at=timezone.now())

        # Termine dieser und nächster Woche
        monday = today - timedelta(days=today.weekday())
        slot = 0
        for week in (0, 1):
            for day in range(5):
                for hour in (8, 9 + idx, 14):
                    if (day + hour + idx + week) % 3:
                        continue
                    patient = patients[slot % len(patients)]
                    start = timezone.make_aware(datetime.combine(monday + timedelta(days=day + 7 * week), time(hour, 0)))
                    Termin.objects.create(patient=patient, start=start, duration_min=45 if slot % 2 else 30,
                                          status=Termin.Status.ERLEDIGT if start < timezone.now() else Termin.Status.GEPLANT)
                    slot += 1

        # Honorarnoten: bezahlt, offen, überfällig (mit Zahlungserinnerung), Entwurf
        def invoice(patient, days_ago, items, pay=None):
            inv = Honorarnote.objects.create(patient=patient)
            for k, (label, qty, cents) in enumerate(items):
                services.add_position(inv, today - timedelta(days=days_ago + 1 + k), label, qty, cents)
            issued = services.issue(inv, today=today - timedelta(days=days_ago))
            if pay:
                services.add_payment(issued, today - timedelta(days=max(days_ago - pay, 0)), issued.total_cents, today=today)
            return issued

        invoice(patients[0], 40, [("Physiotherapie Einzel (30 Min.)", 3, 5500)], pay=9)
        invoice(patients[1], 35, [("Manuelle Therapie (45 Min.)", 2, 7200), ("Erstbefund und Beratung", 1, 8500)], pay=12)
        invoice(patients[2], 10, [("Physiotherapie Einzel (30 Min.)", 2, 5500)])
        overdue = invoice(patients[3], 38, [("Physiotherapie Einzel (30 Min.)", 4, 5500)])
        services.record_reminder(overdue, today - timedelta(days=15))
        invoice(patients[4 % len(patients)], 30, [("Lymphdrainage (45 Min.)", 3, 6800)])
        draft = Honorarnote.objects.create(patient=patients[-1])
        services.add_position(draft, today - timedelta(days=1), "Physiotherapie Einzel (30 Min.)", 1, 5500)

        # Buchhaltung: eine Ausgabe (die Einnahmen entstehen automatisch aus den Zahlungen)
        books.ensure_categories()
        cat = Kategorie.objects.filter(kind="ausgabe").order_by("pk").first()
        if cat:
            books.create_buchung(today - timedelta(days=20), "ausgabe", cat, 3490 + idx * 500, text="Verbrauchsmaterial (Demo)", counterpart="Fiktiv GmbH")

    # --- Praxis: Eigentümerin, Räume, Miete ---
    def _fill_practice(self, tenants, today):
        from praxis import services as rent
        from praxis.models import Mietverhaeltnis, PraxisProfil
        from raeume.models import Raum, Raumbelegung

        PraxisProfil.objects.create(
            name="Physio-Gemeinschaftspraxis Demo", address="Musterstraße 1\n1010 Wien", iban=IBANS["maria"],
            tax_text="Umsatzsteuerfreie Vermietung. DEMO-TEXT, nicht vom Steuerberater geprüft.", tax_confirmed_on=today, payment_days=14,
        )
        rooms = [Raum.objects.create(name=n, note=note) for n, note in
                 (("Behandlungsraum 1", "Liege, Fenster zum Hof"), ("Behandlungsraum 2", "Zwei Liegen"), ("Gymnastikraum", "Geräte, 20 m²"))]
        monday = today - timedelta(days=today.weekday())
        for i, slug in enumerate(("peter", "sophie", "tom")):
            for day in (0, 2, 4):
                start = timezone.make_aware(datetime.combine(monday + timedelta(days=day), time(8 + i * 3, 0)))
                Raumbelegung.objects.create(room=rooms[i % 3], tenant=tenants[slug], start=start, end=start + timedelta(hours=3),
                                            kind=Raumbelegung.Kind.RESERVIERUNG)
        for slug, rent_cents, utilities in (("peter", 45000, 6000), ("sophie", 38000, 5000), ("tom", 52000, 7000)):
            Mietverhaeltnis.objects.create(tenant=tenants[slug], rent_cents=rent_cents, utilities_cents=utilities,
                                           valid_from=today.replace(day=1) - timedelta(days=100))
        last_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        created, _ = rent.issue_all(last_month, today)
        if created:
            rent.mark_paid(created[0], today, today)
