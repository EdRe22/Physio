from django.db import OperationalError, ProgrammingError

from . import themes


def theme(request):
    """Liefert das CSS der gewählten Praxisfarbe (gilt für alle Seiten, auch die Anmeldung)."""
    from praxis.models import Erscheinungsbild

    try:
        row = Erscheinungsbild.objects.values_list("palette", "custom_color").first()
    except (OperationalError, ProgrammingError):  # Tabelle fehlt noch (vor der Migration)
        row = None
    palette, custom = row or (themes.DEFAULT, "")
    return {"theme_css": themes.css_for(palette, custom)}
