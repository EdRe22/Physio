# Physio-Praxisgemeinschaft: Patienten- und Abrechnungs-App

Konzept: `Konzept WebApp Physio-Praxisgemeinschaft.docx` (bzw. Claude Doc). Stack: Django, SQLite (Entwicklung) bzw. PostgreSQL (Produktion, Raspberry Pi), Datenbank pro Therapeut.

## Stand

| Phase | Inhalt | Stand |
|---|---|---|
| 1 Fundament | Mandantentrennung (eigene DB pro Therapeut), Envelope-Verschlüsselung, Audit-Log mit Hash-Kette, Login mit Pflicht-2FA (TOTP), Sperre nach Fehlversuchen | fertig, getestet |
| 2 Modul A | Patientenstamm (verschlüsselt), Einwilligungen (Modell), Suche, Archivieren | Grundlage fertig |
| 2 Rest | Zuweisungen, Befund/Verlauf, Dokumente, Termine, E-Mail-Erinnerung | offen |
| 3-6 | Excel-Import, Honorarnoten, Buchhaltung, Praxisbereich/Miete, Vertretung, Go-Live | offen |

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
