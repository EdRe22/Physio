#!/bin/bash
# Automatisches Entsperren von Physio nach einem Neustart (systemd-creds, ohne TPM) einrichten oder entfernen.
#
#   sudo bash docs/pi/auto-unlock.sh einrichten
#   sudo bash docs/pi/auto-unlock.sh status
#   sudo bash docs/pi/auto-unlock.sh entfernen
#
# ACHTUNG: Wer den Pi oder ein Abbild der Karte besitzt, kann die Passphrase gewinnen (SECURITY.md). Nur verwenden,
# wenn der Standort gesichert ist. Abweichende Namen: PHYSIO_SERVICE=… PHYSIO_ENVFILE=… davor setzen (nach sudo).
set -euo pipefail

SVC="${PHYSIO_SERVICE:-physio}"
ENVFILE="${PHYSIO_ENVFILE:-/etc/physio.env}"
NAME=physio-passphrase                                   # muss zu core/autounlock.py passen
CRED="/etc/credstore.encrypted/$SVC-passphrase"
DROPIN="/etc/systemd/system/$SVC.service.d/auto-unlock.conf"

die() { echo "✗ $*" >&2; exit 1; }
[ "$(id -u)" = "0" ] || die "Bitte mit sudo starten."

port() { systemctl cat "$SVC" | sed -n 's/.*--bind[ =]127\.0\.0\.1:\([0-9]*\).*/\1/p' | tail -n1; }

# Wartet auf die App und meldet den HTTP-Status der Anmeldeseite (503 = gesperrt).
probe() {
  local p code=000; p="$(port)"; p="${p:-3010}"
  for _ in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$p/login/" || true)"
    [ "$code" != "000" ] && break
    sleep 1
  done
  echo "$code"
}

restart_and_report() {
  systemctl daemon-reload
  systemctl restart "$SVC"
  probe
}

case "${1:-}" in
  einrichten)
    command -v systemd-creds >/dev/null || die "systemd-creds fehlt (systemd ≥ 250 nötig)."
    systemctl cat "$SVC" >/dev/null 2>&1 || die "Dienst '$SVC' nicht gefunden."
    dir="$(systemctl show "$SVC" -p WorkingDirectory --value)"
    user="$(systemctl show "$SVC" -p User --value)"; user="${user:-root}"
    [ -f "$dir/core/autounlock.py" ] || die "Der Code in '$dir' kennt das automatische Entsperren noch nicht – zuerst aktualisieren."
    keyfile="$(sed -n 's/^[[:space:]]*PHYSIO_KEYFILE=//p' "$ENVFILE" | tail -n1 | tr -d '\r"'"'")"
    [ -n "$keyfile" ] || die "PHYSIO_KEYFILE steht nicht in $ENVFILE."

    read -r -s -p "Betriebs-Passphrase von Physio: " pass; echo
    [ -n "$pass" ] || die "Keine Eingabe."
    # Erst prüfen, dann speichern. Läuft als Dienstbenutzer (nie App-Code als root); Passphrase nur über stdin.
    printf '%s' "$pass" | (cd "$dir" && runuser -u "$user" -- "$dir/.venv/bin/python" -c '
import sys
from core import keystore
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        keystore.open_sealed(fh.read(), sys.stdin.buffer.read().decode("utf-8"))
except (OSError, keystore.KeyUnlockError) as exc:
    sys.exit(f"{exc}")
' "$keyfile") || die "Die Passphrase öffnet die Schlüsseldatei nicht (oder es ist zusätzlich ein USB-Faktor nötig – dann ist automatisches Entsperren nicht möglich). Nichts verändert."

    install -d -m 700 "$(dirname "$CRED")"
    tmp="$CRED.neu"; rm -f "$tmp"
    printf '%s' "$pass" | systemd-creds encrypt --with-key=host --name="$NAME" - "$tmp"
    unset pass
    chmod 600 "$tmp"; mv -f "$tmp" "$CRED"
    install -d -m 755 "$(dirname "$DROPIN")"
    printf '[Service]\nLoadCredentialEncrypted=%s:%s\n' "$NAME" "$CRED" > "$DROPIN"
    code="$(restart_and_report)"
    case "$code" in
      200|302) echo "✓ Eingerichtet: Physio ist nach dem Neustart entsperrt (HTTP $code)." ;;
      *) die "Eingerichtet, aber die App antwortet mit HTTP $code. Prüfen: journalctl -u $SVC -n 50 — zurücknehmen: sudo bash $0 entfernen" ;;
    esac
    ;;
  entfernen)
    rm -f "$DROPIN" "$CRED" "$CRED.neu"
    code="$(restart_and_report)"
    echo "✓ Entfernt. Physio antwortet mit HTTP $code (503 = gesperrt, bitte unter /entsperren/ freigeben)."
    ;;
  status)
    if [ -f "$DROPIN" ] && [ -f "$CRED" ]; then echo "Automatisches Entsperren: eingerichtet"; else echo "Automatisches Entsperren: nicht eingerichtet"; fi
    echo "Anmeldeseite: HTTP $(probe)  (200/302 = entsperrt, 503 = gesperrt)"
    journalctl -u "$SVC" -b --no-pager 2>/dev/null | grep -i 'entsperr' | tail -n 3 || true
    ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
