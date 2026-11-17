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
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and getattr(user, "theme_palette", ""):
        palette, custom = user.theme_palette, user.theme_custom  # eigene Farbe hat Vorrang vor der Praxisfarbe
    return {"theme_css": themes.css_for(palette, custom)}
