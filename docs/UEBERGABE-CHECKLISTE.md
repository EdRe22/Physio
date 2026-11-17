# Übergabe-Checkliste: Entwicklung → Betrieb

Für den Sys-Admin (technische Betreuung) und den Eigentümer. Ergänzt `DEPLOYMENT.md` (Technik im Detail) und
`SECURITY.md` (bekannte Restrisiken). Jeder Punkt wird mit Datum und Namen abgehakt. **Echte Patientendaten kommen erst
ins System, wenn Teil A bis E vollständig erledigt sind.**

Stand der Software: entwickelt und getestet nur mit erfundenen Daten, unter SQLite. **PostgreSQL, Raspberry Pi und
Hardware-Token wurden noch nie erprobt.** Ein Abhaken in Teil C ist daher keine Formalität, sondern die erste echte Prüfung.

Legende: **[Admin]** macht die technische Betreuung, **[Eigentümer]** die Praxisleitung, **[Beide]** gemeinsam.

---

## A. Verträge und Rechtliches (vor allem anderen)

| | Punkt | Wer | Erledigt (Datum/Name) |
|---|---|---|---|
| ☐ | Auftragsverarbeitungsvertrag (Art. 28 DSGVO) zwischen Praxis und Sys-Admin unterschrieben | Beide | |
| ☐ | Jeder Kollege ist als eigener Verantwortlicher für seine Patientendaten informiert und hat der Nutzung zugestimmt | Eigentümer | |
| ☐ | Datenschutz-Folgenabschätzung durchgeführt (Gesundheitsdaten, umfangreiche Verarbeitung) | Eigentümer | |
| ☐ | Verarbeitungsverzeichnis je Verantwortlichem angelegt | Eigentümer | |
| ☐ | Steuerberater hat den Wortlaut der Befreiungshinweise schriftlich bestätigt: Honorarnoten **und** Vermietung | Eigentümer | |
| ☐ | Aufbewahrungsfristen rechtlich bestätigt (im Code angenommen: Dokumentation 10 Jahre, Honorarnoten/Buchhaltung 7 Jahre, siehe `praxis/offboarding.py`) | Eigentümer | |
| ☐ | Wortlaut der Mahnschreiben (Zahlungserinnerung, 1. Mahnung, letzte Mahnung in `rechnungen/pdf.py`), Mahnspesen und Verzicht auf Verzugszinsen rechtlich bestätigt | Eigentümer | |
| ☐ | Regelung für Tod oder dauerhaften Ausfall eines Kollegen geklärt (Erben, Nachfolger, Berufsrecht). Die App kann das nicht entscheiden | Eigentümer | |
| ☐ | Aufbewahrungsdauer der Sicherungen festgelegt und dokumentiert (Löschung gilt erst nach Ablauf der alten Sicherungen als vollständig) | Beide | |
| ☐ | Meldeprozess für Datenpannen (72 Stunden) besprochen: wer meldet an wen | Beide | |
| ☐ | Schriftliche Bestätigung des Eigentümers, dass der Verlust von Passphrase **und** Wiederherstellungsdatei den unwiederbringlichen Datenverlust bedeutet | Eigentümer | |

## B. Server einrichten (`DEPLOYMENT.md` Abschnitte 1–3)

| | Punkt | Wer | Erledigt |
|---|---|---|---|
| ☐ | Raspberry Pi 5, 64-Bit-OS, automatische Sicherheitsupdates, SSH nur mit Schlüssel | Admin | |
| ☐ | Datenpartition und Sicherungsziel mit **LUKS** verschlüsselt, Schlüssel nicht auf dem Gerät | Admin | |
| ☐ | Zugriff nur über **VPN**, kein App-Port im Internet, Firewall nur VPN und SSH | Admin | |
| ☐ | nginx mit TLS, `X-Forwarded-For` wird **überschrieben** (`$remote_addr`), `X-Forwarded-Proto` gesetzt | Admin | |
| ☐ | nginx: `location /intern/ { deny all; }` | Admin | |
| ☐ | fail2ban auf die Anmeldeseite | Admin | |
| ☐ | NTP aktiv (Zeitstempel im Protokoll, Fristen) | Admin | |
| ☐ | PostgreSQL mit zwei Rollen (`physio_owner` für Migrationen, `physio_app` für den Betrieb), Rechte wie in Abschnitt 2 | Admin | |
| ☐ | `/etc/physio.env` nur für root und Dienstbenutzer lesbar; `PHYSIO_SECRET_KEY` lang und zufällig; `PHYSIO_ALLOWED_HOSTS` gesetzt (inkl. `127.0.0.1` für die Cron-Aufrufe) | Admin | |
| ☐ | `PHYSIO_DEV` ist **nicht** gesetzt. In Produktion darf nie `PHYSIO_DEV=1` laufen (sperrt sonst die Schlüsselprüfung) | Admin | |
| ☐ | Genau **ein** Gunicorn-Prozess (`--workers 1 --threads 4`) | Admin | |
| ☐ | SMTP eingerichtet, TLS erzwungen (`PHYSIO_EMAIL_*`), Testmail kommt an | Admin | |
| ☐ | `PHYSIO_JOB_TOKEN` (mind. 32 Zeichen) in der App-Umgebung und in `/etc/physio.jobtoken` (Rechte 600) | Admin | |

## C. Schlüssel, Sicherung, Wiederherstellung (die wichtigste Prüfung)

| | Punkt | Wer | Erledigt |
|---|---|---|---|
| ☐ | `init_master_key` ausgeführt, Betriebs-Passphrase kennt nur der Eigentümer | Beide | |
| ☐ | **Wiederherstellungsdatei + eigene Passphrase** an getrenntem Ort (Tresor), danach aus `/tmp` gelöscht | Eigentümer | |
| ☐ | Neustart-Test: App antwortet 503, nach `/entsperren/` läuft alles, falsche Passphrase wird ab dem 3. Versuch gebremst | Beide | |
| ☐ | Tägliche Sicherung per Cron läuft, `PHYSIO_BACKUP_PASSPHRASE` aus separater Datei | Admin | |
| ☐ | Mindestens eine Sicherungskopie **außerhalb** des Pi, verschlüsselt | Admin | |
| ☐ | **Wiederherstellungsübung** auf einem zweiten Gerät: `restore_backup`, Entsperren mit der Wiederherstellungsdatei, `verify_audit` grün, Stichprobe Patient und Beleg lesbar | Admin | |
| ☐ | Übung dokumentiert und ein Wiederholungstermin vereinbart (mindestens jährlich) | Beide | |
| ☐ | Cron-Aufgaben laufen und Fehler gehen per Mail an den Admin (Tabelle in `DEPLOYMENT.md` Abschnitt 4); ein Aufruf bei gesperrter App schlägt sichtbar fehl | Admin | |

## D. Sicherheitsprüfung

| | Punkt | Wer | Erledigt |
|---|---|---|---|
| ☐ | `manage.py check --deploy` ohne Fehler | Admin | |
| ☐ | **PostgreSQL:** `UPDATE`, `DELETE`, `TRUNCATE` auf `audit_auditentry` als `physio_app` schlagen fehl (Trigger + Rechte). Bisher nie gegen echtes PostgreSQL getestet | Admin | |
| ☐ | Alle automatischen Tests laufen auch gegen PostgreSQL grün (derzeit nur SQLite) | Admin | |
| ☐ | Entscheidung zur fehlenden Datenbank-Sperre gegen Doppelbuchung von Räumen (`EXCLUDE USING gist`, nur wichtig bei mehreren Prozessen) | Beide | |
| ☐ | Mandantentrennung von Hand geprüft: Therapeut B öffnet URLs von Therapeut A → immer 404/403 | Beide | |
| ☐ | Der Verwaltungsbereich ist für Kollegen gesperrt (403), für den Eigentümer erreichbar | Beide | |
| ☐ | Abhängigkeiten geprüft (`pip-audit`), Django auf dem neuesten 6.0.x-Stand | Admin | |
| ☐ | **Penetrationstest durch Dritte** (Schwerpunkte: Mandantentrennung, Login/2FA, Datei-Uploads inkl. Logo und Import, Notfallzugriff) | Eigentümer | |
| ☐ | Befunde des Tests behoben und nachgeprüft | Admin | |

## E. Einrichtung der Praxis in der App (durch den Eigentümer, Menü „Verwaltung“)

Die Seite „Verwaltung → Einrichtung“ zeigt den Stand und führt zu jedem Schritt.

| | Punkt | Erledigt |
|---|---|---|
| ☐ | Erstes Konto des Eigentümers angelegt (`create_tenant`, `create_therapist … --owner`), zweiter Faktor eingerichtet, Passwort geändert | |
| ☐ | Praxisprofil (Miete): Name, Anschrift, IBAN, Zahlungsziel, **vom Steuerberater bestätigter** Hinweis; optional Logo | |
| ☐ | Eigenes Rechnungsprofil (Einstellungen) mit bestätigtem Befreiungshinweis; optional Logo | |
| ☐ | Behandlungsräume angelegt | |
| ☐ | Praxisfarbe gewählt (Verwaltung → Farben) | |
| ☐ | Kollegen angelegt (Verwaltung → Benutzer → Therapeut anlegen); Startpasswort persönlich übergeben, **gemeinsam** zweiten Faktor einrichten | |
| ☐ | Mietverhältnisse je Kollege (Miete, Betriebskosten, Gültigkeit) | |
| ☐ | Mindestens drei aktive Therapeuten, sonst ist der Notfallzugriff nicht nutzbar | |
| ☐ | Leistungskatalog je Therapeut (Bezeichnung, Preis) | |
| ☐ | Probe-Honorarnote und Probe-Mietabrechnung erzeugt, **PDF geprüft** (Angaben, Logo, Hinweise, Beträge), danach storniert | |
| ☐ | Zweiten Faktor eines Testkontos zurückgesetzt und neu eingerichtet (Ablauf „Handy verloren“ geübt) | |
| ☐ | Notfallzugriff einmal mit Testkonten durchgespielt (Antrag, Freigabe, Ablauf, Beenden) | |

## F. Schulung und Unterlagen

| | Punkt | Wer | Erledigt |
|---|---|---|---|
| ☐ | Jeder Kollege hat die Hilfe in der App („Hilfe“ in der Seitenleiste) gesehen, mit Schwerpunkt: zweiter Faktor, Passwort, automatische Abmeldung | Eigentümer | |
| ☐ | Jeder Kollege weiß: Gesamtexport selbst ziehen und sicher aufbewahren, Notfallzugriff nur im Ernstfall (jeder Zugriff wird protokolliert) | Eigentümer | |
| ☐ | Eigentümer kennt die Abläufe: Entsperren nach Neustart, Passwort und Faktor zurücksetzen, Ausscheiden, Protokoll prüfen | Admin | |
| ☐ | Kontaktweg zum Admin bei Störungen und Datenpannen festgelegt | Beide | |
| ☐ | Passphrase-Verwaltung besprochen: keine Weitergabe, keine Speicherung im Browser oder Chat | Beide | |

## G. Start (Echtbetrieb)

| | Punkt | Wer | Erledigt |
|---|---|---|---|
| ☐ | Teil A bis F vollständig abgehakt und von beiden unterschrieben | Beide | |
| ☐ | Testdaten und Testkonten entfernt (nur echte Konten bleiben) | Admin | |
| ☐ | Import der Bestandsdaten: zuerst mit **einem** Kollegen und einer kleinen Datei, Vorschau prüfen, dann die übrigen | Beide | |
| ☐ | Erste Woche: täglicher Blick auf Protokollprüfung (Verwaltung → „Jetzt prüfen“), Sicherungsmeldungen und Cron-Mails | Admin | |
| ☐ | Nach vier Wochen Rückblick: Störungen, offene Punkte, Wiederholung der Wiederherstellungsübung terminiert | Beide | |

---

## Bewusst offene Punkte (siehe `SECURITY.md`)

Auch nach vollständiger Abnahme bleiben, gewollt oder technisch bedingt:

- **Hardware-Token** für den Master-Schlüssel ist noch nicht angebunden (heute: Passphrase + optional USB-Datei).
- Der Sys-Admin kann mit Root-Rechten am laufenden, entsperrten System Daten lesen. Schutz sind Vertrag, LUKS und Zugriffsbeschränkung.
- Kein IP-Limit beim Login in der App (fail2ban auf dem Server).
- Noch nicht gebaut: E-Mail-Erinnerung für überfällige Mieten, E-Mail-Versand der Honorarnoten, Zuordnung der Miete im Bankabgleich, Belege-Upload.

| Unterschrift Eigentümer | Unterschrift Sys-Admin | Datum |
|---|---|---|
| | | |
