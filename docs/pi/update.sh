#!/usr/bin/env bash
# Update auf dem Pi: Code holen, Pakete, Sicherheitspruefung, Migrationen, Neustart.
# Aufruf im Projektordner: bash docs/pi/update.sh
# Mit STRICT=1 bricht das Skript bei bekannten Schwachstellen ab (sonst nur Warnung).
set -euo pipefail

cd "$(dirname "$0")/../.."   # Projektordner, egal wo er liegt
PY=.venv/bin/python

# Umgebung wie der Dienst (Secret, Hosts, Keyfile ...) laden
set -a; source /etc/physio.env; set +a

git pull --ff-only
$PY -m pip install -r requirements.txt

echo "== Sicherheitspruefung der Pakete (pip-audit)"
$PY -m pip install -q pip-audit
if ! $PY -m pip_audit -r requirements.txt; then
  echo "WARNUNG: bekannte Schwachstellen in Abhaengigkeiten (siehe oben)."
  [ "${STRICT:-0}" = "1" ] && { echo "STRICT=1: Abbruch."; exit 1; }
fi

echo "== Django-Einstellungen (check --deploy)"
# Fehler brechen ab. Warnungen zu HSTS-Subdomains/Preload/SSL-Redirect sind bewusst offen (Caddy macht den Redirect).
$PY manage.py check --deploy

$PY manage.py migrate --noinput
$PY manage.py migrate_tenants

sudo systemctl restart physio
echo "Fertig. Nach dem Neustart ist die App gesperrt: /entsperren/ aufrufen."
