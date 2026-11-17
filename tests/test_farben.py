import pytest
from django.test import Client

from praxis.models import Erscheinungsbild
from verwaltung import themes

from .conftest import make_user, verified_client
from .test_verwaltung import owner, owner_client  # noqa: F401


def _lum(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("p", themes.PALETTES, ids=lambda p: p.slug)
def test_palettes_are_readable(p):
    L, D = p.light, p.dark
    assert contrast("#ffffff", L["brand"]) >= 4.5           # weißer Text auf Schaltfläche, Links auf Weiß
    assert contrast(L["ink"], L["bg"]) >= 7                  # Fließtext
    assert contrast(L["muted"], "#ffffff") >= 4.5            # Nebentext
    assert contrast("#e6f6f3", L["side-to"]) >= 4.5          # Menütext
    assert contrast("#e6f6f3", L["side-from"]) >= 4.5
    assert contrast(D["brand-ink"], D["brand"]) >= 4.5       # dunkler Text auf Schaltfläche im Dunkelmodus
    assert contrast(D["brand"], D["surface"]) >= 4.5         # Links im Dunkelmodus
    assert contrast(D["ink"], D["bg"]) >= 7
    assert contrast(D["muted"], D["surface"]) >= 4.5
    assert contrast("#d9efeb", D["side-to"]) >= 4.5


def test_css_only_contains_known_safe_values():
    for p in themes.PALETTES:
        css = themes.css_for(p.slug)
        assert "<" not in css and "url(" not in css and "expression" not in css


def test_default_has_no_override_and_unknown_is_ignored():
    assert themes.css_for("teal") == "" and themes.css_for("<script>") == ""


def test_only_owner_can_change(client_beta, owner_client):  # noqa: F811
    assert client_beta.get("/verwaltung/erscheinungsbild/").status_code == 403
    assert client_beta.post("/verwaltung/erscheinungsbild/", {"palette": "blau"}).status_code == 403
    assert not Erscheinungsbild.objects.exists()
    assert owner_client.get("/verwaltung/erscheinungsbild/").status_code == 200


def test_choice_applies_to_everyone_including_login(owner_client, client_beta):  # noqa: F811
    r = owner_client.post("/verwaltung/erscheinungsbild/", {"palette": "bordeaux"})
    assert r.status_code == 302 and Erscheinungsbild.objects.get().palette == "bordeaux"
    assert "#9f1239" in client_beta.get("/uebersicht/").content.decode()
    assert "#9f1239" in Client().get("/anmelden/").content.decode() or "#9f1239" in Client().get("/login/").content.decode()
    owner_client.post("/verwaltung/erscheinungsbild/", {"palette": "teal"})
    assert Erscheinungsbild.objects.count() == 1  # Einzelsatz wird aktualisiert, nicht vermehrt


def test_invalid_choice_rejected(owner_client):  # noqa: F811
    owner_client.post("/verwaltung/erscheinungsbild/", {"palette": "blau"})
    owner_client.post("/verwaltung/erscheinungsbild/", {"palette": "}</style><script>x"})
    assert Erscheinungsbild.objects.get().palette == "blau"
