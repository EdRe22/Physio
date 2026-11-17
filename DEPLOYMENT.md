# Betrieb auf dem Raspberry Pi (Produktion)

Stand: Entwurf. Nichts davon wurde auf echter Hardware oder gegen PostgreSQL erprobt. Vor dem Echtbetrieb muss
der Sys-Admin diese Anleitung einmal komplett durchspielen (siehe „Abnahme“).

## 1. Grundsystem

- Raspberry Pi 5, 64-Bit-OS, automatische Sicherheitsupdates, SSH nur mit Schlüssel.
- **Festplattenverschlüsselung (LUKS)** für Datenpartition und Sicherungsziel. Schlüssel nicht auf dem Gerät.
- Erreichbar nur über **VPN** (z. B. WireGuard). Kein Port der App im Internet. Firewall: nur VPN und SSH.
- Reverse-Proxy (nginx) beendet TLS und leitet an `127.0.0.1:8000`. Er MUSS `proxy_set_header X-Forwarded-For $remote_addr;`
  (überschreiben, nicht anhängen) und `X-Forwarded-Proto` setzen, sonst ist die IP im Audit-Log fälschbar.
- fail2ban auf die Anmeldeseite (die App sperrt nur pro Konto, nicht pro IP).
- Uhr per NTP synchron halten (Audit-Zeitstempel, Fristen).

## 2. Datenbank (PostgreSQL)

Zwei Rollen, damit die App den Audit-Log nicht umgehen kann:

```sql
CREATE ROLE physio_owner LOGIN PASSWORD '...';   -- nur für Migrationen/Einrichtung, nie für den Betrieb
CREATE ROLE physio_app   LOGIN PASSWORD '...';   -- Betriebsrolle der App (PHYSIO_DB_USER)
CREATE DATABASE physio_praxis OWNER physio_owner;
-- je Mandant (macht `create_tenant`, danach Rechte vergeben):
GRANT CONNECT ON DATABASE physio_t_<slug> TO physio_app;
\c physio_t_<slug>
GRANT USAGE ON SCHEMA public TO physio_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO physio_app;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_auditentry FROM physio_app;   -- zusätzlich zum Trigger
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO physio_app;
```

Die Trigger gegen Änderungen am Audit-Log (Migration `audit/0002`) sind für PostgreSQL geschrieben,
aber **nicht gegen eine echte PostgreSQL-Instanz getestet**. Abnahmetest siehe unten.

Hinweis: `create_tenant` legt die Mandanten-DB mit dem Benutzer an, der in `PHYSIO_DB_USER` steht. Für den
Betrieb muss der Anlege-/Migrationsvorgang mit `physio_owner` laufen, die App danach mit `physio_app`.

## 3. App

```bash
python -m venv /opt/physio/venv && /opt/physio/venv/bin/pip install -r requirements.txt gunicorn psycopg[binary]
```

Umgebung (z. B. `/etc/physio.env`, nur für root und den Dienstbenutzer lesbar):

```
PHYSIO_SECRET_KEY=<lang, zufällig>
PHYSIO_ALLOWED_HOSTS=praxis.example.at
PHYSIO_DB_ENGINE=postgres
PHYSIO_DB_USER=physio_app
PHYSIO_DB_PASSWORD=...
PHYSIO_TRUST_PROXY=1                                 # echte Client-IP aus X-Forwarded-For (nginx auf Loopback)
PHYSIO_KEYFILE=/opt/physio/keys/master.sealed      # versiegelter Master-Schlüssel
PHYSIO_BELEG_DIR=/srv/physio/belege
PHYSIO_EMAIL_...                                    # SMTP für Terminerinnerungen
```

**Genau ein Serverprozess:** Der entsperrte Master-Schlüssel liegt nur im Arbeitsspeicher. Daher
`gunicorn --workers 1 --threads 4 config.wsgi`. Mehrere Prozesse würden jeweils einzeln entsperrt werden müssen.

### Erstinstallation des Master-Schlüssels (einmalig, vor dem ersten Mandanten)

```bash
python manage.py init_master_key --keyfile /opt/physio/keys/master.sealed --recovery /tmp/master.recovery [--usb-secret /media/usb/faktor.bin]
```

- Betriebs-Passphrase: nur der Eigentümer. Optional zusätzlich eine Datei auf einem USB-Stick als zweiter Faktor.
- **Wiederherstellungsdatei + eigene Passphrase** sofort an einen getrennten Ort (Tresor), danach von `/tmp` löschen.
- Ohne Schlüsseldatei oder Wiederherstellungsdatei samt Passphrase sind **alle Daten unwiederbringlich verloren**.
  Das ist gewollt (Datensparsamkeit) und muss dem Eigentümer schriftlich bewusst sein.
- Ein echter Hardware-Token (z. B. YubiKey) ist **noch nicht angebunden**. Heute: Passphrase + optionale USB-Datei.

### Nach jedem Neustart

Die App antwortet mit 503, bis der Eigentümer unter `https://praxis.example.at/entsperren/` Passphrase
(und ggf. Schlüsseldatei) eingibt. Falsche Eingaben werden ab dem dritten Versuch ausgebremst.

## 4. Regelmäßige Aufgaben (Cron, Dienstbenutzer)

| Zeit | Befehl | Zweck |
|---|---|---|
| täglich 02:00 | `manage.py backup /mnt/backup/physio-$(date +%F).pbk` | verschlüsselte Gesamtsicherung (`PHYSIO_BACKUP_PASSPHRASE` aus separater Datei). Braucht den Master-Schlüssel nicht. |
| täglich 03:00 | `curl -fsS -X POST -H "Authorization: Bearer $(cat /etc/physio.jobtoken)" http://127.0.0.1:8000/intern/job/verify_audit/` | Hash-Kette aller Mandanten prüfen |
| täglich 07:00 | dito `.../intern/job/send_reminders/` | Terminerinnerungen |
| stündlich | dito `.../intern/job/purge_imports/` | Importreste löschen |
| 1. des Monats | dito `.../intern/job/issue_rent/?period=JJJJ-MM` (ohne Parameter: aktueller Monat) | Mietabrechnungen |

**Warum HTTP statt `manage.py`:** Befehle, die Mandanten öffnen, brauchen den Master-Schlüssel, der nur im Speicher der
entsperrten App liegt. Cron ruft sie daher an der laufenden App auf (`core/jobs.py`). Ist die App gesperrt, liefert
der Aufruf 503, `curl -f` schlägt fehl und Cron meldet das per Mail. Das Ergebnis muss überwacht werden.

Voraussetzungen: `PHYSIO_JOB_TOKEN` (mind. 32 Zeichen, zufällig) in der App-Umgebung und in `/etc/physio.jobtoken`
(Rechte 600), `127.0.0.1` in `PHYSIO_ALLOWED_HOSTS`, und in nginx **`location /intern/ { deny all; }`**. Die App
lehnt zusätzlich alles ab, was Proxy-Header trägt oder nicht vom Loopback kommt.

Sicherungen: mindestens eine Kopie **außerhalb** des Pi (anderer Ort, verschlüsselt, z. B. USB-Platte beim
Eigentümer). Belegordner (`PHYSIO_BELEG_DIR`) ist Teil der Sicherung.

## 5. Abnahme (vor Echtdaten, alles abhaken)

1. [ ] `check --deploy` ohne Fehler (Warnungen zu HSTS-Subdomains/Preload bewusst offen, SSL-Redirect macht der Proxy).
2. [ ] Neustart: App ist gesperrt (503), nach `/entsperren/` läuft alles. Falsche Passphrase wird gebremst.
3. [ ] **PostgreSQL:** `UPDATE`/`DELETE`/`TRUNCATE` auf `audit_auditentry` als `physio_app` schlägt fehl (Trigger und Rechte).
4. [ ] **Wiederherstellungsübung:** Sicherung auf einem zweiten Gerät entpacken (`restore_backup`), mit der
   Wiederherstellungsdatei entsperren, `verify_audit` grün, Stichprobe Patient/Beleg lesbar.
5. [ ] Mandantentrennung manuell: Therapeut B kann keine URL von Therapeut A öffnen (404).
6. [ ] Vertrag zur Auftragsverarbeitung mit dem Sys-Admin, Datenschutz-Folgenabschätzung, Verarbeitungsverzeichnis.
7. [ ] Steuerberater hat Befreiungshinweise (Honorarnoten, Miete) schriftlich bestätigt.
8. [ ] Penetrationstest durch Dritte.

## 6. Ausscheiden und Praxisende

1. Der Therapeut zieht unter „Patienten“ den **Gesamtexport** (ZIP, enthält Gesundheitsdaten im Klartext, sicher übergeben).
2. `manage.py offboard_tenant <kürzel> --export-confirmed` (fragt die Passphrase zum Entsperren ab): sperrt Konto und Mandant,
   beendet das Mietverhältnis, berechnet das Fristende (Dokumentation 10 Jahre, Honorarnoten/Buchhaltung 7 Jahre).
   **Die Fristen sind nicht rechtlich bestätigt** (`praxis/offboarding.py`).
3. Nach Fristende `manage.py purge_tenant <kürzel> --confirm <kürzel>`: löscht Datenbank, Belege und Datenschlüssel endgültig.
   Ältere Sicherungen enthalten den Datenschlüssel (geschützt durch den Master-Schlüssel) noch. Die Löschung ist erst
   vollständig, wenn diese Sicherungen ausgetauscht sind. Die Aufbewahrungsdauer der Sicherungen daher festlegen und dokumentieren.

## 7. Notfallzugriff (Vertretung bei Ausfall)

Therapeut beantragt mit Begründung, ein **anderer** Therapeut (weder Antragsteller noch Betroffener) gibt frei.
Gilt 24 Stunden, nur lesend, jederzeit widerrufbar. Alle Schritte und Lesezugriffe stehen im Audit-Log des Betroffenen,
der sie auch auf seiner Notfall-Seite sieht (und per E-Mail erfährt, falls eine Adresse hinterlegt ist).
Voraussetzung: mindestens drei aktive Therapeuten. **Bei dauerhafter Abwesenheit des Betroffenen** (Tod) bleibt die
Frage der Rechtsnachfolge (Erben, Berufsrecht) offen und ist rechtlich zu klären.

## 8. Benutzerverwaltung

- Neues Konto: `manage.py create_tenant`, `manage.py create_therapist` und die Einrichtung des zweiten Faktors
  **gemeinsam mit der Person**, danach sofort Passwort ändern (Menü „Passwort“).
- Verlorenes Handy: Identität persönlich prüfen, dann `manage.py reset_2fa <benutzer>`, Faktor gemeinsam neu einrichten.
- Vergessenes Passwort: `manage.py changepassword <benutzer>`, danach Person bitten, es zu ändern.

## 9. Raumbelegung

Der Raumplan liegt in der gemeinsamen Praxis-Datenbank und enthält nur Raum, Therapeut und Zeitraum. Der Eigentümer legt
die Räume unter „Räume verwalten“ an. Die Konfliktprüfung ist durch eine Sperre im Serverprozess abgesichert
(deshalb genau ein Prozess, siehe Abschnitt 3). **Auf PostgreSQL fehlt noch eine Ausschluss-Bedingung in der Datenbank**
(`EXCLUDE USING gist`), die selbst bei mehreren Prozessen Doppelbuchungen unmöglich machen würde.

## 10. Statische Dateien

Die Oberfläche lädt `static/ui.js` (Formularlayout). Django liefert den Ordner `static/` selbst aus, ein eigener Webserver-Eintrag ist nicht nötig. Die Datei enthält keine Daten und ist ohne Anmeldung abrufbar. Inline-Skripte bleiben durch die Sicherheitsregeln (CSP) verboten.
