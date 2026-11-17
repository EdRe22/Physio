import base64
import io
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from pypdf import PdfReader

from core import logo
from praxis.models import PraxisProfil
from rechnungen.models import Honorarnote, Rechnungsprofil

from .conftest import make_user, verified_client


def png_bytes(size=(300, 120), color=(20, 120, 110), mode="RGB", fmt="PNG"):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format=fmt)
    return buf.getvalue()


def upload(data, name="logo.png", ctype="image/png"):
    return SimpleUploadedFile(name, data, content_type=ctype)


def test_process_reencodes_and_limits_size():
    b64 = logo.process_upload(upload(png_bytes((4000, 1500))))
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert img.format == "PNG" and img.width <= logo.BOX[0] and img.height <= logo.BOX[1]


def test_jpeg_with_metadata_is_stripped():
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x010E] = "GEHEIME-BESCHREIBUNG"
    Image.new("RGB", (200, 100), (200, 30, 30)).save(buf, format="JPEG", exif=exif)
    b64 = logo.process_upload(upload(buf.getvalue(), "l.jpg", "image/jpeg"))
    raw = base64.b64decode(b64)
    assert b"GEHEIME-BESCHREIBUNG" not in raw and raw.startswith(b"\x89PNG")


def test_transparent_logo_gets_white_background():
    b64 = logo.process_upload(upload(png_bytes((100, 100), (0, 0, 0, 0), "RGBA")))
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    assert img.getpixel((5, 5)) == (255, 255, 255)


@pytest.mark.parametrize("data,name", [
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "x.svg"),
    (b"GIF89a" + b"\x00" * 50, "x.gif"),
    (b"%PDF-1.4 nicht wirklich", "x.png"),
    (b"", "leer.png"),
    (png_bytes()[:60], "abgeschnitten.png"),
])
def test_invalid_files_rejected(data, name):
    with pytest.raises(logo.LogoError):
        logo.process_upload(upload(data, name))


def test_too_large_rejected():
    with pytest.raises(logo.LogoError):
        logo.process_upload(upload(b"0" * (logo.MAX_UPLOAD_BYTES + 1)))


def profile_data(**extra):
    base = {"name": "Maria Test", "title": "Physiotherapeutin", "address": "Teststr. 1\n1010 Wien", "phone": "", "email": "",
            "tax_no": "", "uid": "", "iban": "", "bic": "", "payment_days": 14,
            "exemption_text": "Steuerfrei laut Steuerberater.", "exemption_confirmed_on": "2026-01-01"}
    base.update(extra)
    return base


def test_therapist_uploads_and_sees_own_logo_only(client_alpha, client_beta, in_alpha):
    r = client_alpha.post("/einstellungen/profil/", profile_data(logo=upload(png_bytes())))
    assert r.status_code == 302, r.content.decode()[:2000]
    assert client_alpha.get("/einstellungen/profil/logo/")["Content-Type"] == "image/png"
    assert client_alpha.get("/einstellungen/profil/logo/")["X-Content-Type-Options"] == "nosniff"
    assert client_beta.get("/einstellungen/profil/logo/").status_code == 404  # Beta hat keins und sieht nie das von Alpha


def test_bad_upload_shows_error_and_saves_nothing(client_alpha, in_alpha):
    r = client_alpha.post("/einstellungen/profil/", profile_data(logo=upload(b"<svg/>", "x.svg", "image/svg+xml")))
    assert r.status_code == 200 and "PNG oder JPEG" in r.content.decode()
    assert not Rechnungsprofil.objects.exists()


def test_remove_logo(client_alpha, in_alpha):
    client_alpha.post("/einstellungen/profil/", profile_data(logo=upload(png_bytes())))
    assert Rechnungsprofil.objects.get().logo_b64
    client_alpha.post("/einstellungen/profil/", profile_data(remove_logo="on"))
    assert Rechnungsprofil.objects.get().logo_b64 == ""


def test_logo_frozen_in_issued_invoice_and_drawn_in_pdf(client_alpha, in_alpha):
    from datetime import date

    from patients.models import Patient
    from rechnungen import services
    from rechnungen.pdf import render_pdf

    client_alpha.post("/einstellungen/profil/", profile_data(logo=upload(png_bytes())))
    patient = Patient.objects.create(first_name="Pia", last_name="Muster")
    inv = Honorarnote.objects.create(patient=patient)
    inv.positions.create(date=date(2026, 1, 5), description="Behandlung", quantity=1, unit_price_cents=5000)
    inv = services.issue(inv)
    assert json.loads(inv.issuer_snapshot)["logo"]
    with_logo = render_pdf(inv)
    # späteres Entfernen des Logos ändert den ausgestellten Beleg nicht
    client_alpha.post("/einstellungen/profil/", profile_data(remove_logo="on"))
    inv.refresh_from_db()
    again = render_pdf(inv)
    assert len(PdfReader(io.BytesIO(again)).pages[0].images) == 1
    assert len(PdfReader(io.BytesIO(with_logo)).pages[0].images) == 1


def test_rent_profile_logo_owner_only(alpha, beta):
    from accounts.models import Therapist

    owner = make_user(alpha, "chefin")
    owner.role = Therapist.Role.OWNER
    owner.save()
    oc = verified_client(owner)
    other = verified_client(make_user(beta, "kollege"))
    data = {"name": "Praxis", "address": "Weg 1", "iban": "AT611904300234573201", "tax_text": "Steuerfrei lt. StB",
            "tax_confirmed_on": "2026-01-01", "payment_days": 14, "logo": upload(png_bytes())}
    assert oc.post("/praxis/profil/", data).status_code == 302
    assert PraxisProfil.objects.get().logo_b64
    assert oc.get("/praxis/profil/logo/").status_code == 200
    assert other.get("/praxis/profil/logo/").status_code == 403
