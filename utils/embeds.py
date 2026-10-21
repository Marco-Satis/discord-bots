"""
Zentraler Embed-Helper — einheitlicher Stil für ALLE Bot-Nachrichten.

Konsolidiert die verstreuten ``discord.Embed(...)``-Aufrufe. Der Aufbau ist der
**HUD-Stil** (2026-08-13), die Farben seit 2026-10-21 die des Dashboards (C3):
Violett als Grundton für den Normalbetrieb, Bedeutung über Farbe UND Statuspunkt, Kennzahlen-Zeile
mit Fortschrittsbalken im Kopf, Metazeilen als kleiner Subtext.

Wer welche Funktion nimmt:

    base_embed / info_embed / success_embed / ...   klassische Meldungen
    hud_embed(...)                                  Panel mit Kennzahlen + Balken
    utils.panels.hud_panel(...)                     Components-V2-Panel mit Buttons

Restyle passiert **hier** — die Farbkonstanten sind die einzige Quelle. Direkte
Hex-Werte in Cogs sind ein Fehler, kein Stil.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional, Sequence, Tuple, Union

import discord

from utils.ui_kit import meta_row, progress_bar, status_dot, subtext

# --- Semantische Farben (Design C3 „Neon-Lobby“ seit 21.10.2026 — Restyle = NUR hier) ---
# Dieselben Werte wie web/static/c3.css: Violett = Marke, Cyan = in Ordnung,
# Magenta = Fehler, Rosa = Warnung. Bis 21.10. galt die Gold-HUD-Palette.
COLOR_BRAND: int = 0xB84DFF     # Violett — Normalbetrieb, Panels, Boards (--violet)
COLOR_SUCCESS: int = 0x53E7F2   # Cyan — in Ordnung (--ok)
COLOR_ERROR: int = 0xFF3FA4     # Magenta — Fehler (--bad)
COLOR_WARNING: int = 0xFF8FCB   # Rosa — Warnung (--warn)
COLOR_INFO: int = 0xD49BFF      # helles Violett — Hinweise (--violet-text)
COLOR_NEUTRAL: int = 0x9A95AE   # Grau (--text-3)
COLOR_PROGRESS: int = 0x8F6BFF  # Blauviolett — Fortschritt, Level, XP (eigene Farbe, eindeutig)

# Zustand -> (Farbe, Statuspunkt). Ein Aufruf, beide Signale konsistent.
STATE_STYLE = {
    "ok": (COLOR_SUCCESS, "ok"),
    "success": (COLOR_SUCCESS, "ok"),
    "warn": (COLOR_WARNING, "warn"),
    "error": (COLOR_ERROR, "crit"),
    "crit": (COLOR_ERROR, "crit"),
    "info": (COLOR_INFO, "info"),
    "idle": (COLOR_NEUTRAL, "idle"),
    "off": (COLOR_NEUTRAL, "off"),
    "brand": (COLOR_BRAND, "warn"),
    "progress": (COLOR_PROGRESS, "info"),  # Level, XP, laufende Vorgaenge
}

# --- Branding (Platzhalter — später aus Dashboard/Config gesetzt) -----------
_BRAND_FOOTER: Optional[str] = None
_BRAND_ICON: Optional[str] = None


def set_branding(footer: Optional[str] = None, icon_url: Optional[str] = None) -> None:
    """
    Globales Embed-Branding setzen (Footer-Text + Icon).

    Wird beim Bot-Start aus Config/Dashboard aufgerufen. Default ist None -> kein
    Footer.
    """
    global _BRAND_FOOTER, _BRAND_ICON
    if footer is not None:
        _BRAND_FOOTER = footer
    if icon_url is not None:
        _BRAND_ICON = icon_url


def base_embed(
    title: Optional[str] = None,
    description: Optional[str] = None,
    *,
    color: int = COLOR_BRAND,
    timestamp: Union[bool, datetime] = True,
    footer: Optional[str] = None,
) -> discord.Embed:
    """
    Basis-Embed mit einheitlichem Stil (Farbe + optional Timestamp + Footer).

    Args:
        title: Embed-Titel.
        description: Embed-Beschreibung.
        color: Farbe (Default = Brand-Gold; semantische Helfer setzen sie).
        timestamp: ``True`` -> jetzt, ``False`` -> keiner, ``datetime`` -> genau
            dieser Zeitpunkt. Der dritte Fall ist kein Luxus: Aufrufer, die einen
            eigenen Zeitpunkt uebergeben (Berichtsende, ``timezone.utc``), haetten
            ihn sonst still verloren — ein ``datetime`` ist immer wahr und waere
            als blosses „ja, Zeitstempel" gelesen worden.
        footer: Footer-Text; None -> globales Branding-Footer (falls gesetzt).

    Returns:
        Konfiguriertes discord.Embed.
    """
    if isinstance(timestamp, datetime):
        stempel = timestamp
    elif timestamp:
        stempel = datetime.now()
    else:
        stempel = None

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=stempel,
    )
    foot = footer if footer is not None else _BRAND_FOOTER
    if foot:
        # icon_url NUR mitgeben, wenn es eins gibt. discord.utils.MISSING wird
        # von set_footer nicht verworfen, sondern landet als "..." im Feld —
        # Discord antwortet darauf mit 400 "Not a well formed URL" und die
        # ganze Nachricht geht nicht raus.
        if _BRAND_ICON:
            embed.set_footer(text=foot, icon_url=_BRAND_ICON)
        else:
            embed.set_footer(text=foot)
    return embed


def success_embed(title: Optional[str] = None, description: Optional[str] = None,
                  **kwargs) -> discord.Embed:
    """Erfolgs-Embed (grün)."""
    kwargs.setdefault("color", COLOR_SUCCESS)
    return base_embed(title, description, **kwargs)


def error_embed(title: Optional[str] = None, description: Optional[str] = None,
                **kwargs) -> discord.Embed:
    """Fehler-Embed (rot)."""
    kwargs.setdefault("color", COLOR_ERROR)
    return base_embed(title, description, **kwargs)


def warning_embed(title: Optional[str] = None, description: Optional[str] = None,
                  **kwargs) -> discord.Embed:
    """Warn-Embed (bernstein)."""
    kwargs.setdefault("color", COLOR_WARNING)
    return base_embed(title, description, **kwargs)


def info_embed(title: Optional[str] = None, description: Optional[str] = None,
               **kwargs) -> discord.Embed:
    """Info-Embed (blau)."""
    kwargs.setdefault("color", COLOR_INFO)
    return base_embed(title, description, **kwargs)


def neutral_embed(title: Optional[str] = None, description: Optional[str] = None,
                  **kwargs) -> discord.Embed:
    """Neutrales Embed (grau)."""
    kwargs.setdefault("color", COLOR_NEUTRAL)
    return base_embed(title, description, **kwargs)


# --- HUD-Bausteine ---------------------------------------------------------


def state_color(state: str) -> int:
    """Farbe zu einem Zustand (``ok``/``warn``/``error``/``info``/…)."""
    return STATE_STYLE.get(state, STATE_STYLE["info"])[0]


def state_title(state: str, title: str) -> str:
    """Titel mit vorangestelltem Statuspunkt — Farbe allein reicht nicht.

    Rund 8% der Männer sehen Rot und Grün nicht zuverlässig auseinander; der
    Punkt wiederholt die Aussage der Farbe in einer zweiten Dimension.
    """
    return f"{status_dot(STATE_STYLE.get(state, STATE_STYLE['info'])[1])} {title}"


def hud_embed(
    title: str,
    *,
    state: str = "brand",
    meta: Optional[Sequence[Tuple[str, str]]] = None,
    bar: Optional[Tuple[float, float]] = None,
    bar_style: str = "outline",
    description: Optional[str] = None,
    lines: Optional[Iterable[str]] = None,
    footer: Optional[str] = None,
    timestamp: Union[bool, datetime] = True,
) -> discord.Embed:
    """
    Panel-Embed im HUD-Stil: Kennzahlen-Kopf, Fortschrittsbalken, Textzeilen.

    Args:
        title: Titel; bekommt automatisch den Statuspunkt vorangestellt.
        state: Zustand aus :data:`STATE_STYLE` — steuert Farbe und Punkt.
        meta: Kennzahlen als ``[(Wert, Beschriftung), …]``, z.B. ``[("3", "offen")]``.
        bar: ``(Wert, Maximum)`` für den Fortschrittsbalken.
        bar_style: Balkenstil aus :data:`utils.ui_kit.BAR_STYLES`.
        description: freier Text unter dem Kopf.
        lines: weitere Zeilen, werden mit Zeilenumbruch angehängt.
        footer: Footer-Text (None -> Branding).
        timestamp: wie bei :func:`base_embed` — Bool oder konkreter Zeitpunkt.

    Returns:
        discord.Embed im Hausstil.
    """
    kopf: list[str] = []
    if meta:
        kopf.append(meta_row(meta))
    if bar:
        kopf.append(progress_bar(bar[0], bar[1], 10, bar_style))
    if description:
        kopf.append(description)
    if lines:
        kopf.extend(lines)

    return base_embed(
        state_title(state, title),
        "\n".join(kopf) if kopf else None,
        color=state_color(state),
        timestamp=timestamp,
        footer=footer,
    )


def status_field(
    name: str,
    state: str,
    *,
    value: Optional[int] = None,
    maximum: Optional[int] = None,
    note: str = "",
) -> str:
    """
    Eine Statuszeile im HUD-Stil: Punkt, Name, Auslastung, Balken, Notiz.

    Beispiel::

        🟢 **Satisfactory** · `3/8`
        ▰▰▰▰▱▱▱▱▱▱ -# Uptime 4d 2h
    """
    dot = status_dot(STATE_STYLE.get(state, STATE_STYLE["idle"])[1])
    kopf = f"{dot} **{name}**"
    if value is not None and maximum is not None:
        kopf += f" · `{value}/{maximum}`"
        kopf += f"\n{progress_bar(value, maximum, 10)}"
        if note:
            kopf += f" {subtext(note)}"
    elif note:
        kopf += f"\n{subtext(note)}"
    return kopf
