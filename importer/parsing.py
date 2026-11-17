"""Einlesen von XLSX/CSV mit harten Grenzen (Schutz vor Missbrauch und Zip-Bomben)."""
import csv
import io
import zipfile
from datetime import date, datetime

MAX_BYTES = 5 * 1024 * 1024
MAX_UNCOMPRESSED = 50 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLS = 30


class ImportFileError(Exception):
    """Fehler mit anwenderfreundlicher Meldung (enthält nie Dateiinhalt)."""


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # Telefonnummern u. Ä. ohne ".0"
    return str(value).strip()


def _trim(row):
    """Entfernt leere Zellen am Zeilenende (Excel liefert aufgefüllte Zeilen)."""
    row = list(row)
    while row and row[-1] == "":
        row.pop()
    return row


def _read_xlsx(raw: bytes):
    import defusedxml  # noqa: F401  (stellt sicher, dass die Absicherung installiert ist)
    from openpyxl import load_workbook

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED:
                raise ImportFileError("Die Datei ist entpackt zu groß.")
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except ImportFileError:
        raise
    except Exception:
        raise ImportFileError("Die Datei ist keine gültige Excel-Datei (.xlsx).")
    sheet = workbook.worksheets[0] if workbook.worksheets else None
    if sheet is None:
        raise ImportFileError("Die Excel-Datei enthält kein Tabellenblatt.")
    rows = []
    for row in sheet.iter_rows(values_only=True, max_col=MAX_COLS + 1):
        if len(rows) > MAX_ROWS + 1:
            raise ImportFileError(f"Zu viele Zeilen (höchstens {MAX_ROWS}).")
        rows.append(_trim(_cell(v) for v in row))
    workbook.close()
    return rows


def _read_csv(raw: bytes):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportFileError("Die CSV-Datei hat eine unbekannte Zeichenkodierung.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for row in csv.reader(io.StringIO(text), dialect):
        if len(rows) > MAX_ROWS + 1:
            raise ImportFileError(f"Zu viele Zeilen (höchstens {MAX_ROWS}).")
        rows.append(_trim(c.strip() for c in row[: MAX_COLS + 1]))
    return rows


def read_table(uploaded):
    """Liest eine hochgeladene Datei. Rückgabe: (Kopfzeile, Zeilen), alles als Text."""
    name = (uploaded.name or "").lower()
    if uploaded.size > MAX_BYTES:
        raise ImportFileError("Die Datei ist größer als 5 MB.")
    raw = uploaded.read()
    if name.endswith(".xml"):  # Kontoauszug camt.053
        from buchhaltung.camt import CamtError, parse_camt053

        try:
            headers, body = parse_camt053(raw)
        except CamtError as exc:
            raise ImportFileError(str(exc))
        return headers, body
    if name.endswith(".xlsx"):
        rows = _read_xlsx(raw)
    elif name.endswith(".csv"):
        rows = _read_csv(raw)
    else:
        raise ImportFileError("Bitte eine .xlsx-, .csv- oder (Kontoauszug) .xml-Datei hochladen.")
    rows = [r for r in rows if any(r)]  # komplett leere Zeilen weglassen
    if len(rows) < 2:
        raise ImportFileError("Die Datei braucht eine Kopfzeile und mindestens eine Datenzeile.")
    if len(rows) - 1 > MAX_ROWS:
        raise ImportFileError(f"Zu viele Zeilen (höchstens {MAX_ROWS}).")
    headers = rows[0]
    if len(headers) > MAX_COLS:
        raise ImportFileError(f"Zu viele Spalten (höchstens {MAX_COLS}).")
    width = len(headers)
    body = [(r + [""] * width)[:width] for r in rows[1:]]
    return headers, body
