# Demo-Praxis und Installation auf dem Raspberry Pi (neben Sozialverein und Prangerschützen)

Zwei Teile: **A** die Demo-Datenbank (nur erfundene Daten), **B** die Installation auf dem Pi, auf dem schon
`sozialverein` (:3001) und `prangerschuetzen` (:3002) laufen. Die Physio-App ist **Python/Django**, die anderen beiden sind
Node-Apps. Sie teilen sich nur den Pi und Caddy, sonst nichts.

> Stand: nicht auf echter Hardware erprobt. Die Schritte sind aus den Anleitungen der beiden anderen Apps
> (`DEPLOYMENT-PI.md`, `Caddyfile.example`) und aus `DEPLOYMENT.md` dieser App abgeleitet. Beim ersten Durchlauf Schritt für Schritt prüfen.

---

## A. Demo-Datenbank

### Was die Demo enthält
Alles erfunden (Namen, Diagnosen, IBANs, E-Mail-Adressen `…@example.invalid`):

- vier Konten: Eigentümerin **Mag. Maria Muster** und drei Therapeuten (Peter Beispiel, Sophie Probst, Tom Testmann), jeder mit eigener
  Datenbank und eigener persönlicher Farbe (Notfallzugriff braucht mindestens drei aktive Therapeuten)
- je Therapeut 5 bis 7 Patienten mit Zuweisung, Anamnese, Befund und Behandlungen, Termine dieser und der nächsten Woche
- je Therapeut sechs Honorarnoten: 2 bezahlt, 2 offen, 1 **überfällig mit Zahlungserinnerung**, 1 Entwurf; Leistungskatalog, Rechnungsprofil, Buchungen
- Räume, Raumreservierungen, Mietverhältnisse und die Mietabrechnungen des Vormonats (eine bezahlt)
- bei je zwei Patienten die Einwilligung zum E-Mail-Versand

Nicht enthalten: Bankumsätze (die zeigen Sie mit einem eigenen Kontoauszug-Import), echte E-Mails (Adressen sind ungültig, ohne Mailserver wird nichts versendet).

### Auf dem Entwicklungsrechner (Windows)
```powershell
cd "C:\Users\edwar\OneDrive - SOLUTIONBOX Informationstechnologie GmbH\Claude Projekte\Physio"
$env:PHYSIO_DEV="1"; $env:PHYSIO_DATA_DIR="$PWD\demo-data"
..\..\..\venvs\physio\Scripts\python.exe manage.py migrate
..\..\..\venvs\physio\Scripts\python.exe manage.py create_demo --confirm-fake-data --credentials-file "$PWD\demo-data\ZUGANGSDATEN.txt"
..\..\..\venvs\physio\Scripts\python.exe manage.py runserver 127.0.0.1:8001 --noreload
```
Die Demo liegt im Ordner `demo-data` (von git ausgenommen), getrennt von den Entwicklungsdaten in `data`. Die Zugangsdaten stehen in
`demo-data\ZUGANGSDATEN.txt` (zufällig erzeugt, nur lesbar für den Benutzer). Beim ersten Anmelden richtet jede Person ihren zweiten Faktor ein
(Authenticator-App auf dem Handy, wie im Echtbetrieb). Für die Vorführung am besten **vorher** einmal alle vier Konten einrichten.

Der Befehl läuft nur in einer **leeren** Installation und verlangt `--confirm-fake-data`. Neu anlegen: Ordner `demo-data` löschen und die Schritte wiederholen.

### Vorschlag für den Ablauf der Vorführung
1. Als **Peter** anmelden: Übersicht, Patientenakte (Dokumentation, Zuweisung), Termine, Honorarnote ausstellen und als PDF zeigen.
2. **Überfällige**: Mahnstufe erstellen, PDF; **Einstellungen → Mahnwesen** (Fristen, Texte, automatischer Versand), **Mailserver**, **Farbe**.
3. Als **Maria** (Eigentümerin): Verwaltung (Checkliste, Benutzer, Standardfarbe), Buchhaltung → Miete (Abrechnung der Kollegen), Räume.
4. Trennung zeigen: Peter sieht nichts von Sophies Patienten; Einstellungen → Notfallzugriff erklären (Freigabe durch Dritte, 24 Stunden, nur lesend).

---

## B. Installation auf dem Pi

### Festlegungen
| | Wert (anpassen) |
|---|---|
| Benutzer / Ordner | wie bei den anderen Apps: `/home/<PI-USER>/physio` (Code), `/home/<PI-USER>/physio-data` (Daten, Schlüssel) |
| Port (nur lokal) | **3010**, vorher prüfen: `ss -ltnp \| grep -E ':30[0-9]{2}'` (3001 und 3002 sind belegt, weitere Instanzen möglich) |
| Dienst | `physio` (bei Demo: `physio-demo`) |
| Adresse | `physio.<DOMAIN>` bzw. `physio.rendl.at` (eigene Subdomain wie bei den anderen) |
| Datenbank | **SQLite** in `physio-data`. Für die Vorführung ausreichend. Für den Echtbetrieb ist PostgreSQL vorgesehen (`DEPLOYMENT.md`) |

Jede der drei Apps hat eigenen Ordner, eigenen Dienst, eigenen Port, eigene Schlüssel und Datenbank. Das Anmelde-Cookie gilt je Subdomain
(host-only), die Apps stören sich nicht.

### 1. Python-Version prüfen (wichtig)
Diese App braucht **Python 3.12 oder neuer** (Django 6). Raspberry Pi OS „Bookworm“ liefert 3.11, „Trixie“ 3.13.
```bash
python3 --version
```
Bei 3.11: Python 3.13 mit `uv` parallel installieren (ändert das System-Python nicht, die Node-Apps sind nicht betroffen):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv python install 3.13
```

### 2. Code auf den Pi bringen
Die Physio-App hat noch **kein Remote-Repository**. Zwei Wege:

- **Empfohlen:** privates GitHub-Repository anlegen, `git remote add origin …`, `git push -u origin master`; auf dem Pi `git clone` (wie bei den anderen Apps, Deploy-Key).
- **Ohne GitHub:** vom Entwicklungsrechner kopieren, ohne Daten und lokale Umgebung:
  ```powershell
  git archive --format=zip -o physio.zip HEAD      # nur versionierte Dateien (kein data/, demo-data/, keine Passwörter)
  scp physio.zip <PI-USER>@<PI-HOST>:/home/<PI-USER>/
  ```
  ```bash
  mkdir -p ~/physio && cd ~/physio && unzip ../physio.zip
  ```

### 3. Umgebung einrichten
```bash
cd ~/physio
~/.local/bin/uv venv --python 3.13 .venv        # oder: python3 -m venv .venv
.venv/bin/pip install -r requirements.txt gunicorn
mkdir -p ~/physio-data
```
Umgebungsdatei `/etc/physio-demo.env` (Rechte `600`, Besitzer root; der Dienst liest sie über `EnvironmentFile`):
```
PHYSIO_SECRET_KEY=<openssl rand -hex 32>
PHYSIO_ALLOWED_HOSTS=physio.rendl.at,127.0.0.1
PHYSIO_TRUST_PROXY=1
PHYSIO_DATA_DIR=/home/<PI-USER>/physio-data
PHYSIO_KEYFILE=/home/<PI-USER>/physio-data/master.sealed
PHYSIO_JOB_TOKEN=<openssl rand -hex 32>
# Mailserver der Praxis (optional; jeder Therapeut kann in den Einstellungen einen eigenen hinterlegen):
# PHYSIO_EMAIL_HOST=…  PHYSIO_EMAIL_USER=…  PHYSIO_EMAIL_PASSWORD=…  PHYSIO_FROM_EMAIL=…
```
Wird die Umgebung nur per `export` gesetzt, gelten die Variablen für die folgenden Befehle in diesem Terminal.
Dieselben Variablen brauchen alle `manage.py`-Befehle unten.

### 4. Master-Schlüssel, Datenbank, Demo-Daten
```bash
set -a; . /etc/physio-demo.env; set +a
cd ~/physio
.venv/bin/python manage.py init_master_key --keyfile "$PHYSIO_KEYFILE" --recovery ~/master.recovery
```
Passphrase wählen und **sicher notieren** (Demo: einfach, aber nicht verlieren). Wiederherstellungsdatei wegsichern und von der Platte löschen.
Ohne Passphrase sind die Daten nicht lesbar, auch für die Demo. Danach:
```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py create_demo --confirm-fake-data --credentials-file ~/physio-data/ZUGANGSDATEN.txt
```
Der Befehl fragt die Passphrase zum Entsperren ab und legt die vier Demo-Konten samt Daten an. Zugangsdaten stehen am Ende auf dem Bildschirm und in
der Datei (nach der Vorführung löschen).
```bash
.venv/bin/python manage.py check --deploy
```

### 5. Dienst (systemd)
`/etc/systemd/system/physio-demo.service`:
```ini
[Unit]
Description=Physio-Praxisverwaltung (Demo)
After=network.target

[Service]
User=<PI-USER>
WorkingDirectory=/home/<PI-USER>/physio
EnvironmentFile=/etc/physio-demo.env
# Genau EIN Prozess: Der entsperrte Master-Schlüssel liegt nur im Arbeitsspeicher dieses Prozesses.
ExecStart=/home/<PI-USER>/physio/.venv/bin/gunicorn --workers 1 --threads 4 --bind 127.0.0.1:3010 config.wsgi
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now physio-demo
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3010/login/    # 503 = gesperrt (richtig), 200 = entsperrt
```

### 6. Caddy: Eintrag ergänzen (die beiden anderen Blöcke unverändert lassen)
In `/etc/caddy/Caddyfile` einen weiteren Block hinzufügen:
```
physio.rendl.at {
	encode zstd gzip
	# Interne Schnittstelle nie von außen:
	@intern path /intern/*
	respond @intern 404
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "same-origin"
		-Server
	}
	reverse_proxy 127.0.0.1:3010 {
		header_up X-Forwarded-For {remote_host}     # überschreibt, damit die IP im Protokoll nicht fälschbar ist
		header_up X-Forwarded-Proto {scheme}
	}
}
```
```bash
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```
- Die App setzt ihre eigenen Sicherheitsregeln (CSP) selbst. Keine CSP in Caddy ergänzen, sonst gelten zwei.
- **Erreichbarkeit:** Für die Vorführung entweder Subdomain per DNS/Let's Encrypt (Pi aus dem Internet erreichbar) oder nur im Heimnetz/VPN mit `tls internal`
  (Caddy-eigene Zertifizierungsstelle, die Geräte müssen ihr vertrauen). Auch Demo-Daten gehören nicht ungeschützt ins Netz:
  zusätzlich `basic_auth` im Caddy-Block oder nur VPN/LAN.
- Die Zeile `header_up X-Forwarded-Proto` ist nötig, sonst lehnt Django Formulare über HTTPS ab (CSRF-Herkunftsprüfung). Die App wertet sie nur mit `PHYSIO_TRUST_PROXY=1` aus.

### 7. Entsperren und Test
Im Browser `https://physio.rendl.at/entsperren/` öffnen, Passphrase eingeben. Danach Anmeldung mit einem Demo-Konto (zweiten Faktor einrichten).
**Nach jedem Neustart des Pi oder des Dienstes** muss neu entsperrt werden (Absicht: Der Schlüssel liegt nie im Klartext auf der Platte).
Bei gesichertem Standort lässt sich das automatisieren: `DEPLOYMENT.md`, „Optional: automatisch entsperren“ (für diese Anleitung mit
`sudo PHYSIO_SERVICE=physio-demo PHYSIO_ENVFILE=/etc/physio-demo.env bash docs/pi/auto-unlock.sh einrichten`).

### 8. Aktualisieren und zurücksetzen
- **Update:** neuen Code einspielen (`git pull` oder neues ZIP), dann
  `sudo systemctl stop physio-demo` → `.venv/bin/pip install -r requirements.txt` → `manage.py migrate` und `manage.py migrate_tenants` → `sudo systemctl start physio-demo` → entsperren. Vorher die Daten sichern (`manage.py backup …`).
- **Demo neu anlegen:** Dienst stoppen, `physio-data` leeren (Schlüsseldatei ebenfalls), Schritte 4 bis 7 wiederholen.
- **Echtbetrieb später:** nicht diese Instanz „umwidmen“. Eine **neue** Installation mit PostgreSQL, Hardware-Schlüssel und Abnahme nach `DEPLOYMENT.md` aufsetzen, die Demo danach löschen.

### 9. Platz und Last
Eine Python-Instanz mit einem Prozess braucht rund 150 bis 250 MB Arbeitsspeicher. Auf einem Pi 5 mit den beiden Node-Apps unproblematisch; `free -h`
und `instances-status.sh` der anderen App danach prüfen. Die Cron-Aufgaben aus `DEPLOYMENT.md` §4 braucht die Demo nicht.
