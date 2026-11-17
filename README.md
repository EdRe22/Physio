# Physio-Praxisgemeinschaft: Patienten- und Abrechnungs-App

Konzept: `Konzept WebApp Physio-Praxisgemeinschaft.docx` (bzw. Claude Doc). Stack: Django, SQLite (Entwicklung) bzw. PostgreSQL (Produktion, Raspberry Pi), Datenbank pro Therapeut.

## Stand

| Phase | Inhalt | Stand |
|---|---|---|
| 1 Fundament | Mandantentrennung (eigene DB pro Therapeut), Envelope-Verschlüsselung, Audit-Log mit Hash-Kette, Login mit Pflicht-2FA (TOTP), Sperre nach Fehlversuchen | fertig, getestet |
| 2 Modul A | Patientenstamm (verschlüsselt), Zuweisungen mit Einheitenzähler und Gültigkeit, versionierte Dokumentation (Anamnese, Befund, Behandlung, Verlauf), Einwilligungen mit Widerruf, Archivieren statt Löschen | fertig, getestet |
| 2 Modul B | Terminkalender pro Therapeut (Woche), Serientermine, Überschneidungsprüfung, E-Mail-Erinnerung (`send_reminders`, nur mit Einwilligung, ohne Gesundheitsdaten) | fertig, getestet |
| 2 Rest | Dokumenten-Upload (Befunde, Fotos), Raumbelegung (gemeinsam, mit Modul E) | offen |
| 3 Import | Excel/CSV-Import für Patientenstamm und Dokumentation: Spaltenzuordnung, Trockenlauf, Dublettenprüfung, Protokoll ohne Zellwerte, Datei wird nie gespeichert | fertig, getestet |
| 4a Honorarnoten | Leistungskatalog, Honorarnoten aus Behandlungen oder frei, fortlaufende lückenlose Nummer pro Jahr, Unveränderlichkeit nach dem Ausstellen, Storno, Zahlungen, Mahnstufen, PDF | fertig, getestet |
| 4b Buchhaltung | Einnahmen-Ausgaben-Journal (unveränderlich, Storno per Gegenbuchung), verschlüsselte Belege, Bankimport (camt.053/CSV/Excel), Zahlungsabgleich, Jahresauswertung, CSV/ZIP-Export für den Steuerberater | fertig, getestet |
| 5 Praxisbereich | Mietverhältnisse pro Therapeut, monatliche Mietabrechnung (Nummernkreis, PDF, unveränderlich, Storno), Zahlung beim Eigentümer als Einnahme, Übernahme beim Kollegen als Ausgabe; Daten nur Beträge, Sichtbarkeit zentral in `services.visible_invoices` | fertig, getestet |
| 6a Produktionshärtung | versiegelter Master-Schlüssel (Passphrase + optionale USB-Datei), Entsperrseite nach Neustart, Wiederherstellungsdatei, DB-Trigger gegen Audit-Manipulation (SQLite getestet, PostgreSQL ungetestet), verschlüsselte Gesamtsicherung mit Wiederherstellung, `verify_audit`, interne Cron-Schnittstelle | fertig, getestet (außer PostgreSQL und echtes Hardware-Token) |
| 6b Vertretung/Praxisende | Datenkopie je Patient und Gesamtexport, Ausscheiden (Sperre, Frist 10/7 Jahre, Löschung per Schlüsselvernichtung), Notfallzugriff mit Vier-Augen-Freigabe, 24 h, nur lesend, im Protokoll des Betroffenen | fertig, getestet (Fristen rechtlich zu bestätigen) |
| 6c Sicherheitsprüfung | eigene Prüfung (siehe SECURITY.md): Passwortwechsel, Proxy-IP, Session-Erneuerung, 2FA-Reset; Rundumtests für Anmeldung, 2FA und Mandantentrennung über alle Routen | fertig, Penetrationstest durch Dritte steht aus |
| 6d Raumbelegung | gemeinsamer Raumplan (nur Raum, Therapeut, Zeit, nie Patient), Konfliktprüfung über alle Therapeuten, Wochenserien alles-oder-nichts, Freie-Räume-Suche, Termine mit Raum buchen/verschieben/freigeben automatisch | fertig, getestet |
| 6e Hilfe | eingebaute Hilfe mit 16 Themen, Suche, Kontext-Link in der Kopfzeile, Eigentümer-Themen nur für den Eigentümer, Zahlen direkt aus dem Code | fertig, getestet |
| 6f |  Hardware-Token, PostgreSQL-Tests, Go-Live (siehe DEPLOYMENT.md, Abnahme) | offen |

**Honorarnoten: Ausstellen ist gesperrt**, bis im Rechnungsprofil das Datum „vom Steuerberater bestätigt“ gesetzt ist. Der Platzhaltertext für die Umsatzsteuerbefreiung wird abgelehnt. Das PDF wird bei Bedarf aus den festgeschriebenen Daten erzeugt und nicht gespeichert (Schrift Roboto aus dem Paket `font-roboto`).

Import: `/import/`. Liegengebliebene Zwischendaten verwirft `python manage.py purge_imports` (täglich per Timer). Grenzen: 5 MB, 5000 Zeilen, 30 Spalten, Formeln werden nicht ausgewertet.

Erinnerungen täglich auslösen (Cron/systemd-Timer auf dem Pi): `python manage.py send_reminders` (Standard: Termine in den nächsten 36 Stunden). SMTP über `PHYSIO_EMAIL_HOST`, `PHYSIO_EMAIL_USER`, `PHYSIO_EMAIL_PASSWORD`, `PHYSIO_FROM_EMAIL`; ohne Konfiguration wird in Produktion nichts versendet.

## Entwicklung (nur mit Fake-Daten!)

```powershell
py -m venv C:\Users\<name>\venvs\physio        # Venv außerhalb von OneDrive
C:\Users\<name>\venvs\physio\Scripts\pip install -r requirements.txt
$env:PHYSIO_DEV="1"
python manage.py migrate
python manage.py create_tenant alpha "Therapeut Alpha"
python manage.py create_therapist alpha_user alpha     # fragt das Passwort ab
python manage.py runserver
python -m pytest
```

Nach jedem Release: `python manage.py migrate` und `python manage.py migrate_tenants`.

## Architekturregeln (verbindlich)

1. **Patientendaten nur in Mandanten-Apps** (`TENANT_APP_LABELS` in den Einstellungen). Diese Apps liegen nie in der Praxis-Datenbank. Der Router erzwingt das.
2. **Kein Standardmandant.** Ohne aktiven Mandanten wirft jeder Zugriff `TenantNotSet`.
3. **Personenbezogene Felder** sind `EncryptedTextField`. Suche erfolgt nach dem Entschlüsseln in der Anwendung, nie per SQL.
4. **Keine Patientendaten** in Logs, Fehlermeldungen, `__str__`, Audit-Einträgen oder URLs.
5. **Jede Schreiboperation** auf Patientendaten wird über Signale auditiert, jedes Lesen explizit im View.
6. **Neue Mandanten-Module**: Modell in einer App aus `TENANT_APP_LABELS`, Tests in `tests/test_isolation.py` ergänzen (fremder Mandant darf nichts sehen).
7. **Keine neuen Abhängigkeiten** ohne Begründung und Freigabe (Lockfile, Schwachstellen-Scan).

## Regeln für den KI-Agenten

- Arbeitet ausschließlich mit erfundenen Daten, ohne Zugang zum Produktivsystem (Raspberry Pi).
- Erhält keine echten Logs oder Importdateien, nur vom Admin bereinigte Fehlerberichte.
- Jedes Release braucht die Freigabe durch den Admin oder einen unabhängigen Reviewer, alle Tests müssen grün sein.

## Offen vor dem Produktivbetrieb (Phase 0)

AV-Verträge, DSFA-Vorprüfung, Steuerberater-Check, Pi-Härtung (VPN, LUKS, Master-Schlüssel vom Hardware-Token), Backup- und Restore-Konzept, Penetrationstest. Siehe Konzept.
