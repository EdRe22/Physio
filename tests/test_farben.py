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


# ---- Palette aus dem Logo ---------------------------------------------------------------------------------------------
import io  # noqa: E402

from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from PIL import Image  # noqa: E402

from core import logo  # noqa: E402

BASE_COLORS = ["#ff0000", "#ffff00", "#00ffff", "#00ff00", "#0000ff", "#ff00ff", "#ffd700", "#a3e4d7", "#ff7f50", "#808000",
               "#123456", "#fafad2"]


@pytest.mark.parametrize("base", BASE_COLORS)
def test_derived_palette_is_readable_for_any_color(base):
    p = themes.derive_palette(base)
    L, D = p.light, p.dark
    assert contrast("#ffffff", L["brand"]) >= 4.5
    assert contrast("#e6f6f3", L["side-to"]) >= 4.5 and contrast("#e6f6f3", L["side-from"]) >= 4.5
    assert contrast(L["ink"], L["bg"]) >= 7 and contrast(L["muted"], "#ffffff") >= 4.5
    assert contrast(D["brand-ink"], D["brand"]) >= 4.5 and contrast(D["brand"], D["surface"]) >= 4.5
    assert contrast(D["ink"], D["bg"]) >= 7 and contrast(D["muted"], D["surface"]) >= 4.5
    assert contrast("#d9efeb", D["side-to"]) >= 4.5


def test_derive_rejects_garbage():
    for bad in ("", "red", "#12345", "#gggggg", "}</style>", "#FF0000"):
        with pytest.raises(ValueError):
            themes.derive_palette(bad)
    assert themes.css_for("logo", "}</style><script>") == ""


def _png(color, size=(200, 80)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _b64(data):
    return logo.process_upload(SimpleUploadedFile("l.png", data, content_type="image/png"))


def test_dominant_color_found_and_grayscale_ignored():
    assert logo.dominant_color(_b64(_png((200, 30, 60)))).startswith("#c8")
    assert logo.dominant_color(_b64(_png((120, 120, 120)))) is None
    assert logo.dominant_color(_b64(_png((255, 255, 255)))) is None
    assert logo.dominant_color("") is None


def test_logo_palette_flow(owner_client):  # noqa: F811
    page = owner_client.get("/verwaltung/erscheinungsbild/").content.decode()
    assert "Zuerst im Praxisprofil ein farbiges Logo" in page
    owner_client.post("/verwaltung/erscheinungsbild/", {"palette": "logo"})
    assert not Erscheinungsbild.objects.exists()  # ohne Logo nicht wählbar
    data = {"name": "Praxis", "address": "Weg 1", "iban": "AT611904300234573201", "tax_text": "Steuerfrei lt. StB",
            "tax_confirmed_on": "2026-01-01", "payment_days": 14,
            "logo": SimpleUploadedFile("l.png", _png((200, 30, 60)), content_type="image/png")}
    owner_client.post("/praxis/profil/", data)
    owner_client.post("/verwaltung/erscheinungsbild/", {"palette": "logo"})
    theme = Erscheinungsbild.objects.get()
    assert theme.palette == "logo" and theme.custom_color.startswith("#c8")
    assert theme.custom_color in owner_client.get("/verwaltung/erscheinungsbild/").content.decode()
    # neues Logo in anderer Farbe: Praxisfarbe zieht nach
    data["logo"] = SimpleUploadedFile("l.png", _png((20, 60, 200)), content_type="image/png")
    owner_client.post("/praxis/profil/", data)
    theme.refresh_from_db()
    assert theme.palette == "logo" and theme.custom_color.endswith("c8")
    # Logo entfernen: zurück zum Standard
    data.pop("logo")
    data["remove_logo"] = "on"
    owner_client.post("/praxis/profil/", data)
    theme.refresh_from_db()
    assert theme.palette == "teal" and theme.custom_color == ""
