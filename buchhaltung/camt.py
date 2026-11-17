"""Parser für Kontoauszüge im Format camt.053 (ISO 20022), das österreichische Banken anbieten.

Liefert Kopfzeile und Zeilen im selben Format wie ein Excel-Import, damit derselbe geprüfte Weg
(Spaltenzuordnung, Trockenlauf, Protokoll) genutzt wird. XML wird mit defusedxml gelesen
(keine externen Entitäten, keine DTD, kein Billion-Laughs).
"""
from decimal import Decimal, InvalidOperation

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

HEADERS = ["Buchungsdatum", "Betrag", "Name", "IBAN", "Verwendungszweck", "Referenz"]
MAX_ENTRIES = 5000


class CamtError(Exception):
    pass


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _children(element, name):
    return [c for c in element if _local(c.tag) == name]


def _first(element, *path):
    """Erstes Element entlang eines Pfades lokaler Namen (Namensräume werden ignoriert)."""
    nodes = [element]
    for name in path:
        nodes = [c for n in nodes for c in _children(n, name)]
        if not nodes:
            return None
    return nodes[0]


def _text(element, *path):
    node = _first(element, *path)
    return (node.text or "").strip() if node is not None else ""


def _amount(node, sign):
    try:
        value = Decimal((node.text or "").strip())
    except InvalidOperation:
        raise CamtError("Ungültiger Betrag im Kontoauszug.")
    if node.get("Ccy", "EUR") != "EUR":
        raise CamtError("Nur Umsätze in EUR werden unterstützt.")
    return f"{sign * value:.2f}"


def parse_camt053(raw: bytes):
    try:
        root = ElementTree.fromstring(raw, forbid_dtd=True)
    except (ElementTree.ParseError, DefusedXmlException):
        raise CamtError("Die XML-Datei ist ungültig oder enthält unzulässige Konstrukte.")
    if "camt.053" not in (root.tag or "") and _first(root, "BkToCstmrStmt") is None:
        raise CamtError("Das ist kein camt.053-Kontoauszug.")

    rows = []
    for entry in (e for e in root.iter() if _local(e.tag) == "Ntry"):
        credit = _text(entry, "CdtDbtInd") == "CRDT"
        reversal = _text(entry, "RvslInd").lower() == "true"
        sign = (1 if credit else -1) * (-1 if reversal else 1)
        day = _text(entry, "BookgDt", "Dt") or _text(entry, "BookgDt", "DtTm")[:10] or _text(entry, "ValDt", "Dt")
        if not day:
            raise CamtError("Ein Umsatz hat kein Buchungsdatum.")
        details = [d for dtls in _children(entry, "NtryDtls") for d in _children(dtls, "TxDtls")]
        batch = len(details) > 1 and all(_first(d, "AmtDtls", "TxAmt", "Amt") is not None for d in details)
        for detail in (details if batch else details[:1] or [None]):
            amount_node = _first(detail, "AmtDtls", "TxAmt", "Amt") if batch else _first(entry, "Amt")
            if amount_node is None:
                raise CamtError("Ein Umsatz hat keinen Betrag.")
            party = ("Dbtr", "DbtrAcct") if credit else ("Cdtr", "CdtrAcct")  # Gegenseite des Kontoinhabers
            name = iban = purpose = reference = ""
            if detail is not None:
                name = _text(detail, "RltdPties", party[0], "Nm")
                iban = _text(detail, "RltdPties", party[1], "Id", "IBAN")
                purpose = " ".join(
                    (n.text or "").strip() for n in detail.iter() if _local(n.tag) == "Ustrd" and (n.text or "").strip()
                )
                reference = _text(detail, "Refs", "EndToEndId")
                if reference.upper() == "NOTPROVIDED":
                    reference = ""
                reference = reference or _text(detail, "RmtInf", "Strd", "CdtrRefInf", "Ref")
            purpose = purpose or _text(entry, "AddtlNtryInf")
            rows.append([day[:10], _amount(amount_node, sign), name, iban, purpose, reference])
            if len(rows) > MAX_ENTRIES:
                raise CamtError(f"Zu viele Umsätze (höchstens {MAX_ENTRIES}).")
    if not rows:
        raise CamtError("Der Kontoauszug enthält keine Umsätze.")
    return HEADERS, rows
