from django.core import mail  # noqa: F401

from accounts.models import Therapist
from praxis.models import Erscheinungsbild
from verwaltung import themes

from .test_logo import png_bytes, upload
from .test_rechnungen import profile


def user_of(client):
    return Therapist.objects.get(pk=client.session["_auth_user_id"])


def test_default_color_until_own_chosen(client_alpha, client_beta, alpha, beta):
    Erscheinungsbild.objects.create(palette="blau")
    blue = themes.css_for("blau", "")
    assert blue in client_alpha.get("/einstellungen/konto/").content.decode()
    assert client_alpha.post("/einstellungen/farbe/", {"palette": "bordeaux"}).status_code == 302
    mine = client_alpha.get("/einstellungen/konto/").content.decode()
    assert themes.css_for("bordeaux", "") in mine and blue not in mine
    assert blue in client_beta.get("/einstellungen/konto/").content.decode()  # Beta bleibt bei der Praxisfarbe
    assert user_of(client_alpha).theme_palette == "bordeaux" and user_of(client_beta).theme_palette == ""


def test_back_to_practice_default_and_invalid_rejected(client_alpha, alpha):
    client_alpha.post("/einstellungen/farbe/", {"palette": "gruen"})
    client_alpha.post("/einstellungen/farbe/", {"palette": "gibtsnicht"})
    assert user_of(client_alpha).theme_palette == "gruen"
    client_alpha.post("/einstellungen/farbe/", {"palette": ""})
    assert user_of(client_alpha).theme_palette == ""


def test_logo_color_for_own_account(client_alpha, in_alpha):
    profile()
    assert "Zuerst im Rechnungsprofil" in client_alpha.get("/einstellungen/farbe/").content.decode()
    client_alpha.post("/einstellungen/farbe/", {"palette": "logo"})
    assert user_of(client_alpha).theme_palette == ""  # ohne Logo nicht möglich
    from .test_logo import profile_data
    client_alpha.post("/einstellungen/profil/", profile_data(logo=upload(png_bytes())))
    assert "aus Hauptfarbe" in client_alpha.get("/einstellungen/farbe/").content.decode()
    client_alpha.post("/einstellungen/farbe/", {"palette": "logo"})
    u = user_of(client_alpha)
    assert u.theme_palette == "logo" and u.theme_custom
    client_alpha.post("/einstellungen/profil/", profile_data(remove_logo="on"))
    assert user_of(client_alpha).theme_palette == ""  # Logo weg -> wieder Praxisfarbe


def test_account_page_and_nav_entry_for_everyone(client_alpha, client_beta, alpha, beta):
    for c in (client_alpha, client_beta):
        page = c.get("/einstellungen/konto/").content.decode()
        assert "Passwort ändern" in page and 'href="/einstellungen/"' in page
