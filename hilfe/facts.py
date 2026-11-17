"""Zahlen und Regeln für die Hilfetexte, direkt aus dem Code gelesen. So kann die Hilfe nicht von der Wirklichkeit abweichen."""
from django.conf import settings


def facts() -> dict:
    from buchhaltung import camt, services as books
    from importer import parsing
    from notfall import models as notfall
    from praxis import offboarding
    from raeume import services as rooms
    from rechnungen import services as billing
    from rechnungen.models import KLEINBETRAG_CENTS

    return {
        "login_max_failures": settings.LOGIN_MAX_FAILURES,
        "login_lock_minutes": settings.LOGIN_LOCK_MINUTES,
        "session_minutes": settings.SESSION_COOKIE_AGE // 60,
        "min_password": 12,
        "notfall_hours": int(notfall.DURATION.total_seconds() // 3600),
        "notfall_min_reason": notfall.MIN_REASON,
        "room_max_hours": rooms.MAX_HOURS,
        "series_max": rooms.MAX_SERIES,
        "beleg_mb": books.MAX_BELEG_BYTES // (1024 * 1024),
        "import_mb": parsing.MAX_BYTES // (1024 * 1024),
        "import_rows": parsing.MAX_ROWS,
        "import_cols": parsing.MAX_COLS,
        "camt_rows": camt.MAX_ENTRIES,
        "kleinbetrag_eur": KLEINBETRAG_CENTS // 100,
        "max_reminder_level": billing.MAX_REMINDER_LEVEL,
        "retention_doc_years": offboarding.RETENTION_DOC_YEARS,
        "retention_tax_years": offboarding.RETENTION_TAX_YEARS,
    }
