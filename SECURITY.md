# Sicherheitsprüfung (Stand: Phase 6b, eigene Prüfung des Entwicklers)

**Das ersetzt keinen Penetrationstest durch Dritte.** Geprüft wurde der Code der App mit Mustersuche, einer Analyse
aller Ansichten und automatisierten Tests. Nicht geprüft: Betriebssystem, nginx, VPN, PostgreSQL-Betrieb, Hardware.

## Methode

- Suche nach riskanten Mustern (`csrf_exempt`, `mark_safe`, Roh-SQL, `eval`, `subprocess`, Weiterleitungen mit Eingaben).
- Analyse aller Ansichten: Jede trägt eine Anmelde-/Mandanten-/Eigentümer-Prüfung, jede schreibende Ansicht ist
  durch POST und CSRF geschützt (Ausnahmen: nur die bewusste Cron-Schnittstelle mit Token).
- Automatisierte Rundumtests (`tests/test_security.py`):
  - jede der über 60 Routen verweigert anonyme Anfragen (GET und POST),
  - jede Route verlangt den zweiten Faktor,
  - ein anderer Therapeut ruft über 150 Objekt-URLs (GET und POST, IDs 1–3) von fremden Daten auf: nie Inhalt, nie
    Änderung, nie Serverfehler (mit Gegenprobe, dass der Eigentümer dieselben Adressen sehen kann).

## Gefunden und behoben

| Befund | Schwere | Behebung |
|---|---|---|
| Kein Passwortwechsel möglich (Startpasswort für immer gültig) | hoch | Passwortwechsel nur mit zweitem Faktor, Regeln aus den Passwort-Validatoren, Audit-Eintrag |
| Audit-Log hätte hinter nginx immer 127.0.0.1 gespeichert | mittel | `core/net.py`: letzte X-Forwarded-For-Adresse, nur mit `PHYSIO_TRUST_PROXY=1` und Verbindung vom Loopback |
| Session-ID blieb nach dem zweiten Faktor gleich | niedrig | Session-ID wird nach 2FA erneuert |
| Riesiger Jahresparameter im Journal führte zu Serverfehler | niedrig | Wertebereich 2000–2100 |
| Kein Weg bei verlorenem Handy (Aussperrung) | betrieblich | `manage.py reset_2fa <benutzer>`: Faktor löschen, Sitzungen beenden, Audit-Eintrag |

## Bekannte Restrisiken (bewusst nicht behoben oder nicht behebbar)

1. **Aussperren per Fehlversuche (Denial of Service):** Nach 5 Fehlversuchen ist ein Konto 15 Minuten gesperrt; die
   Entsperrseite bremst ab dem 3. Fehlversuch. Wer die Seite erreicht, kann Kollegen kurzzeitig aussperren.
   Gegenmaßnahme: nur per VPN erreichbar (siehe DEPLOYMENT.md).
2. **Erst-Einrichtung des zweiten Faktors:** Wer das Startpasswort kennt, bevor die Person den Faktor einrichtet, könnte
   seinen eigenen Faktor registrieren. Gegenmaßnahme organisatorisch: Konto und Einrichtung des Faktors gemeinsam
   mit der Person durchführen, dann sofort das Passwort ändern. Dasselbe gilt nach `reset_2fa`.
3. **Kein IP-basiertes Limit** beim Login (nur pro Konto). Fail2ban o. ä. auf dem Server ergänzen.
4. **CSP erlaubt Inline-Styles** (`style-src 'unsafe-inline'`), Skripte sind nicht erlaubt (`default-src 'self'`).
5. **Ein Prozess:** Der entsperrte Master-Schlüssel liegt im Arbeitsspeicher. Wer Root auf dem Server hat oder einen
   Speicherabzug bekommt, kann ihn lesen. Das ist bei einem Server, der Daten verarbeiten muss, nicht vermeidbar;
   Schutz sind LUKS, Zugriffsbeschränkung und Auftragsverarbeitungsvertrag mit dem Admin.
6. **Der Sys-Admin ist vertrauenswürdig vorausgesetzt:** Mit Root-Rechten und dem laufenden, entsperrten System
   kann er Daten lesen. Der Notfall-Zugriff und das Audit-Log schützen vor Kollegen, nicht vor dem Betreiber.
7. **PostgreSQL-Teile ungetestet** (Trigger, Rollenrechte, `pg_dump`).
8. **Hardware-Token nicht angebunden:** Passphrase plus optionale USB-Datei.
9. **Abhängigkeiten:** `pip-audit` o. ä. regelmäßig laufen lassen, Django-Sicherheitsupdates einspielen
   (Django 6.0.x). Das ist nicht automatisiert.
10. **E-Mail-Erinnerungen** enthalten keine Gesundheitsdaten, der Versand läuft aber über das SMTP des Betreibers
    (TLS erzwingen).
11. **Automatisches Entsperren (optional, standardmäßig aus):** Mit `docs/pi/auto-unlock.sh` liegt die Passphrase
    verschlüsselt auf dem Pi, der Schlüssel dazu (`/var/lib/systemd/credential.secret`, ohne TPM) auf derselben Karte.
    **Diebstahl des Pi oder ein Abbild der Karte hebt damit den Schutz des versiegelten Master-Schlüssels auf.**
    Akzeptiert nur bei gesichertem Standort (alarmgesicherter Raum); bei Standortwechsel neu bewerten. Außerdem
    kann jeder Prozess unter demselben Benutzer wie der Dienst die bereitgestellte Datei möglicherweise lesen:
    Vor Echtdaten braucht Physio einen eigenen Systembenutzer.
12. **Demo ohne zweiten Faktor (optional, standardmäßig aus):** `PHYSIO_DEMO_OHNE_2FA=1` lässt die vier Demo-Konten
    nur mit Passwort hinein (`core/demo.py`), auch das Eigentümer-Konto. Nur mit erfundenen Daten; vor Echtdaten entfernen.

## Empfehlung vor Echtbetrieb

Penetrationstest durch Dritte (Schwerpunkt Mandantentrennung, Login/2FA, Datei-Uploads, Import), anschließend die
Abnahme-Checkliste in DEPLOYMENT.md.
