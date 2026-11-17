from django.db import OperationalError, ProgrammingError

from . import themes


def theme(request):
    """Liefert das CSS der gewählten Praxisfarbe (gilt für alle Seiten, auch die Anmeldung)."""
    from praxis.models import Erscheinungsbild

    try:
        choice = Erscheinungsbild.objects.values_list("palette", flat=True).first()
    except (OperationalError, ProgrammingError):  # Tabelle fehlt noch (vor der Migration)
        choice = None
    return {"theme_css": themes.css_for(choice or themes.DEFAULT)}
