from django.urls import reverse

from .topics import topic_for_path


def help_link(request):
    """Hilfe-Link der Kopfzeile: führt direkt zum Thema des aktuellen Bereichs."""
    slug = topic_for_path(request.path)
    return {"help_url": reverse("hilfe_topic", args=[slug]) if slug else reverse("hilfe_index")}
