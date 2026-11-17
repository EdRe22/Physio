"""Logo für Honorarnoten und Mietabrechnungen.

Sicherheitsprinzip: Hochgeladene Bilder werden nie unverändert gespeichert. Das Bild wird mit Pillow geprüft, in
eine feste Höchstgröße umgerechnet und als neues PNG kodiert. Dabei verschwinden Metadaten (z. B. Standort in
Fotos) und eingebettete Zusatzdaten. Nur PNG und JPEG sind erlaubt, SVG nicht (kann Skripte enthalten).
Das fertige Logo liegt als Base64-Text in der Datenbank und wird beim Ausstellen in die festgeschriebenen
Aussteller-Angaben der Beleg-Kopie übernommen, damit ausgestellte Belege später nicht anders aussehen.
"""
import base64
import io

from django import forms
from django.http import HttpResponse
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_SOURCE_PIXELS = 25_000_000  # Schutz vor „Dekompressionsbomben“
BOX = (900, 360)  # Höchstmaße des gespeicherten Logos in Pixeln (reicht für scharfen Druck bei ca. 6 cm Breite)
ALLOWED_FORMATS = {"PNG", "JPEG"}


class LogoError(ValueError):
    pass


def process_upload(upload) -> str:
    """Prüft das Bild und liefert das neu kodierte PNG als Base64-Text. Wirft LogoError mit verständlicher Meldung."""
    if upload.size > MAX_UPLOAD_BYTES:
        raise LogoError("Die Datei ist zu groß (höchstens 2 MB).")
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise LogoError("Bitte ein Bild im Format PNG oder JPEG hochladen.")
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:  # verify() macht das Objekt unbrauchbar, daher neu öffnen
            img.load()
            img = ImageOps.exif_transpose(img)
            if img.width < 16 or img.height < 16:
                raise LogoError("Das Bild ist zu klein.")
            rgba = img.convert("RGBA")
    except LogoError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise LogoError("Die Datei ist kein gültiges Bild (PNG oder JPEG).")
    background = Image.new("RGB", rgba.size, (255, 255, 255))  # Transparenz auf Weiß (Beleg ist weiß)
    background.paste(rgba, mask=rgba.getchannel("A"))
    background.thumbnail(BOX, Image.LANCZOS)
    out = io.BytesIO()
    background.save(out, format="PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii")


def decode(b64: str) -> bytes:
    return base64.b64decode(b64) if b64 else b""


def image_response(b64: str):
    """Antwort für die Vorschau im Browser. Kein Zwischenspeichern, kein Erraten des Typs."""
    if not b64:
        return HttpResponse(status=404)
    response = HttpResponse(decode(b64), content_type="image/png")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def draw_on_pdf(pdf, b64: str, *, max_w=55, max_h=22):
    """Setzt das Logo oben rechts auf die aktuelle Seite (Position bezogen auf den rechten Rand)."""
    if not b64:
        return
    with Image.open(io.BytesIO(decode(b64))) as img:
        w, h = img.size
    scale = min(max_w / w, max_h / h)
    width, height = w * scale, h * scale
    pdf.image(io.BytesIO(decode(b64)), x=pdf.w - pdf.r_margin - width, y=pdf.t_margin, w=width, h=height)


class LogoFormMixin(forms.ModelForm):
    """Ergänzt ein Profilformular um Logo-Upload und „Logo entfernen“. Das Modell braucht das Feld `logo_b64`."""

    logo = forms.FileField(label="Logo (PNG oder JPEG, optional)", required=False,
                           help_text="Erscheint oben rechts auf Ihren Belegen. Höchstens 2 MB, wird automatisch verkleinert.",
                           widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg"}))
    remove_logo = forms.BooleanField(label="Logo entfernen", required=False)

    def clean_logo(self):
        upload = self.cleaned_data.get("logo")
        if not upload:
            return None
        try:
            self._logo_b64 = process_upload(upload)
        except LogoError as exc:
            raise forms.ValidationError(str(exc))
        return upload

    def save(self, commit=True):
        obj = super().save(commit=False)
        if getattr(self, "_logo_b64", None):
            obj.logo_b64 = self._logo_b64
        elif self.cleaned_data.get("remove_logo"):
            obj.logo_b64 = ""
        if commit:
            obj.save()
        return obj
