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


def css_for(slug: str) -> str:
    """Überschreibt die Farbvariablen der Oberfläche. Die Standardpalette braucht keine Überschreibung."""
    palette = BY_SLUG.get(slug)
    if palette is None or slug == DEFAULT:
        return ""
    return (f":root{{{_decls(palette.light)}}}"
            f"@media (prefers-color-scheme: dark){{:root{{{_decls(palette.dark)}}}}}")
