"""
UI-Baukasten — Bausteine fuer den Embed-/Panel-Stil aller Bots.

Zweck: die visuellen Elemente, die ein Panel „nicht nach Standard-Embed"
aussehen lassen, stehen an EINER Stelle — Fortschrittsbalken, Status-Punkte,
Kapazitaets-Anzeige, feine Trenner, relative Zeitstempel, Subtext.

Bewusst ohne Discord-Abhaengigkeit ausser fuer die Farb-Palette: die Helfer
liefern Strings und lassen sich in klassischen Embeds genauso benutzen wie in
Components-V2-Layouts (TextDisplay).

    from utils.ui_kit import progress_bar, status_dot, subtext, rel_time

    progress_bar(7, 20)                 -> "▰▰▰▱▱▱▱▱▱▱"
    progress_bar(7, 20, style="outline")-> "▬▬▬▭▭▭▭▭▭▭"
    status_dot("warn")                  -> "🟡"
    subtext("3 offen")                  -> "-# 3 offen"

Mobile zuerst: Discord bricht auf dem Handy frueh um. Deshalb kurze Zeilen,
keine breiten Tabellen, Balken maximal ~12 Zeichen.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, Optional, Tuple

# --- Fortschritts-/Kapazitaetsbalken ---------------------------------------
# (voll, leer) — Stil-Name -> Zeichenpaar
BAR_STYLES: Dict[str, Tuple[str, str]] = {
    "box": ("▰", "▱"),        # gefuellt/leer, kompakt — Standard
    "outline": ("▬", "▭"),    # wie in Marcos Referenz-Panel
    "block": ("█", "░"),      # kraeftig, sehr kontrastreich
    "dot": ("●", "○"),        # dezent
    "line": ("━", "─"),       # sehr fein, fast unsichtbar auf Handy
}

# --- Status-Punkte ---------------------------------------------------------
DOTS: Dict[str, str] = {
    "ok": "🟢",
    "warn": "🟡",
    "crit": "🔴",
    "idle": "⚪",
    "off": "⚫",
    "info": "🔵",
}

# --- Feine Trenner ---------------------------------------------------------
RULE_THIN = "─" * 18
RULE_DOTS = "· " * 12


def progress_bar(
    value: float,
    maximum: float,
    width: int = 10,
    style: str = "box",
) -> str:
    """Balken aus Zeichen — ``value`` von ``maximum`` auf ``width`` Zeichen.

    Args:
        value: aktueller Wert (wird auf 0..maximum begrenzt).
        maximum: Maximalwert; <= 0 ergibt einen leeren Balken.
        width: Zeichenbreite (Handy: <= 12 halten).
        style: Schluessel aus :data:`BAR_STYLES`.
    """
    full, empty = BAR_STYLES.get(style, BAR_STYLES["box"])
    if maximum <= 0 or width <= 0:
        return empty * max(width, 0)
    ratio = min(max(value / maximum, 0.0), 1.0)
    filled = int(round(ratio * width))
    # Angefangener Fortschritt soll sichtbar sein, fertig heisst wirklich voll.
    if 0 < ratio < 1:
        filled = min(max(filled, 1), width - 1)
    return full * filled + empty * (width - filled)


def status_dot(state: str) -> str:
    """Farbiger Punkt fuer einen Zustand (``ok``/``warn``/``crit``/``idle``/…)."""
    return DOTS.get(state, DOTS["idle"])


def subtext(text: str) -> str:
    """Discord-Subtext (kleiner, gedimmt) — ``-# text``."""
    return f"-# {text}"


def heading(text: str, level: int = 2) -> str:
    """Discord-Ueberschrift; Level 1-3, alles andere wird fett gesetzt."""
    if level in (1, 2, 3):
        return f"{'#' * level} {text}"
    return f"**{text}**"


def rel_time(when: Optional[datetime]) -> str:
    """Relativer Zeitstempel (``vor 3 Minuten``) — von Discord lokalisiert."""
    if when is None:
        return "—"
    return f"<t:{int(when.timestamp())}:R>"


def abs_time(when: Optional[datetime]) -> str:
    """Kurzer absoluter Zeitstempel (Uhrzeit, lokal beim Betrachter)."""
    if when is None:
        return "—"
    return f"<t:{int(when.timestamp())}:t>"


def capacity(value: int, maximum: int, icon: str = "👥") -> str:
    """Kapazitaets-Anzeige wie ``👥 7/20``."""
    return f"{icon} `{value}/{maximum}`"


def zahl(wert: float) -> str:
    """Zahl mit deutschem Tausenderpunkt: ``12345`` -> ``12.345``.

    Python formatiert mit ``,`` als Trenner; im deutschen Panel-Text ist das
    ein Dezimalkomma und liest sich falsch.
    """
    return f"{wert:,.0f}".replace(",", ".")


def chip(text: str) -> str:
    """Kleiner Monospace-Chip — fuer Zahlenpaare, Versionen, Zustaende.

    Monospace, damit Ziffern beim Aktualisieren nicht springen.
    """
    return f"`{text}`"


def meta_row(pairs: Iterable[Tuple[str, str]], sep: str = " · ") -> str:
    """Kennzahlen-Zeile: ``**3** offen · **2** erledigt · **40%** fertig``."""
    return sep.join(f"**{value}** {label}" for value, label in pairs)


def kv_line(label: str, value: str, pad: int = 0) -> str:
    """Beschriftete Zeile ``Label · Wert``, optional auf ``pad`` ausgerichtet."""
    return f"{label.ljust(pad) if pad else label} · {value}"


def bullet_list(items: Iterable[str], marker: str = "›") -> str:
    """Kompakte Liste mit schlankem Marker statt Discord-Aufzaehlung."""
    return "\n".join(f"{marker} {i}" for i in items)


def truncate(text: str, limit: int) -> str:
    """Text auf ``limit`` Zeichen kuerzen, mit … am Ende."""
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"
