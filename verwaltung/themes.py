"""Farbpaletten der Oberfläche. Bewusst eine feste Auswahl statt freier Farbwahl: Jede Palette ist auf Lesbarkeit
(Kontrast mindestens 4,5 : 1 für Text und Schaltflächen, hell und dunkel) geprüft, siehe tests/test_farben.py.
Die Werte stammen ausschließlich aus dieser Datei, nie aus Benutzereingaben (deshalb darf das CSS direkt ausgegeben werden)."""
from dataclasses import dataclass

DEFAULT = "teal"

LIGHT_KEYS = ("bg", "line", "line-strong", "ink", "muted", "brand", "brand-2", "brand-soft", "side-from", "side-to")
DARK_KEYS = ("bg", "surface", "line", "line-strong", "ink", "muted", "brand", "brand-2", "brand-ink", "brand-soft", "side-from", "side-to")


@dataclass(frozen=True)
class Palette:
    slug: str
    name: str
    light: dict
    dark: dict


def _p(slug, name, light, dark):
    return Palette(slug, name, dict(zip(LIGHT_KEYS, light)), dict(zip(DARK_KEYS, dark)))


PALETTES = [
    _p("teal", "Türkis (Standard)",
       ("#eef5f3", "#dfebe8", "#bfd3cf", "#15302d", "#5a7471", "#0f766e", "#14a39a", "#def3ef", "#0a4a45", "#0f766e"),
       ("#0c1615", "#13211f", "#223633", "#345049", "#e5f3f0", "#8fb0ab", "#3cc9bb", "#5fe0d2", "#04211e", "#173b37", "#07201e", "#0b3a36")),
    _p("blau", "Blau",
       ("#eef2fa", "#dde5f3", "#bccbe6", "#172033", "#596780", "#1d4ed8", "#3b82f6", "#e3ecfd", "#12306b", "#1d4ed8"),
       ("#0b1220", "#121b2e", "#22304d", "#34476b", "#e6edf9", "#9aaccc", "#7aa7ff", "#9bbcff", "#0b1630", "#1a2a4d", "#0a1530", "#112a63")),
    _p("violett", "Violett",
       ("#f3effa", "#e6def4", "#cdbfe6", "#241a36", "#6a5f80", "#6d28d9", "#8b5cf6", "#eee7fb", "#3b1470", "#6d28d9"),
       ("#120e1d", "#1c1630", "#30284a", "#463b6a", "#eee8fb", "#b0a4cf", "#b79cff", "#cdb9ff", "#1a0f33", "#2b2150", "#1a0b33", "#3a1a78")),
    _p("gruen", "Grün",
       ("#eef6f0", "#dcebe0", "#bcd5c4", "#14291c", "#587060", "#15803d", "#22a559", "#def5e6", "#0d4a25", "#127a39"),
       ("#0b150f", "#12211a", "#213a2b", "#335142", "#e3f3e9", "#91b3a0", "#5bd98a", "#86e6a9", "#05210f", "#153a24", "#07200f", "#0c3a1d")),
    _p("bordeaux", "Bordeaux",
       ("#f9eff1", "#f0dde1", "#dfbcc4", "#2e1519", "#7a5a60", "#9f1239", "#e11d48", "#fbe4ea", "#5e0b22", "#9f1239"),
       ("#170c0f", "#211216", "#3a2026", "#55323a", "#f7e6ea", "#c19aa4", "#ff8aa5", "#ffa9bd", "#2b0610", "#3d1822", "#2a0610", "#5a1128")),
    _p("anthrazit", "Anthrazit",
       ("#f1f3f5", "#e1e5ea", "#c3cad3", "#1c232d", "#5b6674", "#334155", "#64748b", "#e8ecf1", "#1e293b", "#334155"),
       ("#0e1217", "#171d25", "#28313d", "#3b4757", "#e8edf3", "#98a6b8", "#a9bbd3", "#c3d1e3", "#0e141c", "#263040", "#0c1118", "#1e2937")),
]
BY_SLUG = {p.slug: p for p in PALETTES}


def _decls(values: dict) -> str:
    return "".join(f"--{k}:{v};" for k, v in values.items())


def css_for(slug: str, custom: str = "") -> str:
    """Überschreibt die Farbvariablen der Oberfläche. Die Standardpalette braucht keine Überschreibung."""
    palette = palette_for(slug, custom)
    if palette is None or slug == DEFAULT:
        return ""
    return (f":root{{{_decls(palette.light)}}}"
            f"@media (prefers-color-scheme: dark){{:root{{{_decls(palette.dark)}}}}}")


# ---- Palette aus einer Einzelfarbe (z. B. Hauptfarbe des Logos) ------------------------------------------------------
import colorsys  # noqa: E402
import re  # noqa: E402

HEX_RE = re.compile(r"^#[0-9a-f]{6}$")
LOGO_SLUG = "logo"


def _rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _hex(h, s, l):
    r, g, b = colorsys.hls_to_rgb(h, min(max(l, 0), 1), min(max(s, 0), 1))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _lum(hex_color):
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in _rgb(hex_color)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def derive_palette(base_hex: str) -> Palette:
    """Erzeugt eine vollständige, kontrastgeprüfte Palette aus einer Farbe. Helligkeit wird so lange angepasst, bis
    Text auf Schaltflächen, Links und Menü die Mindestkontraste (4,5 : 1) einhalten."""
    if not HEX_RE.match(base_hex or ""):
        raise ValueError("Ungültige Farbe.")
    h, _, s = colorsys.rgb_to_hls(*_rgb(base_hex))
    s = min(max(s, 0.45), 0.9)
    # hell
    l = 0.42
    while l > 0.15 and (contrast("#ffffff", _hex(h, s, l)) < 4.8 or contrast("#e6f6f3", _hex(h, s, l)) < 4.6):
        l -= 0.01
    brand = _hex(h, s, l)
    light = dict(zip(LIGHT_KEYS, (
        _hex(h, .30, .96), _hex(h, .28, .91), _hex(h, .22, .80), _hex(h, .35, .14), _hex(h, .12, .40),
        brand, _hex(h, s, min(l + .12, .62)), _hex(h, .55, .93), _hex(h, s, max(l * .55, .12)), brand)))
    # dunkel
    dl = 0.68
    while dl < 0.9 and contrast(_hex(h, .65, dl), _hex(h, .25, .12)) < 4.8:
        dl += 0.01
    dbrand = _hex(h, .65, dl)
    il = 0.12
    while il > 0.02 and contrast(_hex(h, .6, il), dbrand) < 4.8:
        il -= 0.01
    dark = dict(zip(DARK_KEYS, (
        _hex(h, .30, .08), _hex(h, .25, .12), _hex(h, .22, .19), _hex(h, .20, .27), _hex(h, .30, .93), _hex(h, .18, .68),
        dbrand, _hex(h, .65, min(dl + .08, .92)), _hex(h, .6, il), _hex(h, .40, .18), _hex(h, .40, .07), _hex(h, .50, .15))))
    return Palette(LOGO_SLUG, "Aus Praxislogo", light, dark)


def palette_for(slug: str, custom: str = ""):
    if slug == LOGO_SLUG:
        try:
            return derive_palette(custom)
        except ValueError:
            return None
    return BY_SLUG.get(slug)
