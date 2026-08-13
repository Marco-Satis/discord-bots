"""
Panel-Baukasten (Components V2) im HUD-Stil.

Ein Panel ist eine Nachricht, die stehen bleibt und sich aktualisiert: Board,
Serverstatus, Countdown, Alarm. Gegenüber einem Embed kann es einen Button
NEBEN eine Zeile setzen — das ist der Unterschied, der nach App aussieht statt
nach Bot.

    from utils.panels import hud_panel, panel_row

    view = hud_panel(
        "SERVERSTATUS",
        meta=[("3", "Server"), ("4", "Spieler")],
        rows=[panel_row(status_field(...), button)],
        footer_note="aktualisiert " + rel_time(datetime.now()),
    )
    await channel.send(view=view)

Grenzen (Discord, nicht verhandelbar):
  * 40 Komponenten je Nachricht, eine Zeile mit Button kostet 3
  * eine Nachricht kann nicht zwischen Embed und Components V2 wechseln —
    Umstieg heißt neu posten
  * mit Components V2 sind ``content`` und ``embed`` derselben Nachricht tabu
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple, Union

import discord

from utils.embeds import COLOR_BRAND, state_color, state_title
from utils.ui_kit import heading, meta_row, progress_bar, subtext

# Harte Discord-Grenze je Nachricht.
COMPONENT_LIMIT = 40


def panel_row(
    text: str, accessory: Optional[discord.ui.Item] = None
) -> Union[discord.ui.Section, discord.ui.TextDisplay]:
    """Eine Panel-Zeile — mit Button/Thumbnail rechts, oder nur Text."""
    if accessory is None:
        return discord.ui.TextDisplay(text)
    return discord.ui.Section(discord.ui.TextDisplay(text), accessory=accessory)


def action_row(*items: discord.ui.Item) -> discord.ui.ActionRow:
    """Aktionszeile mit bis zu fünf Buttons (oder einem Auswahl-Menü)."""
    row = discord.ui.ActionRow()
    for item in items:
        row.add_item(item)
    return row


def hud_container(
    title: str,
    *,
    state: str = "brand",
    subtitle: str = "",
    meta: Optional[Sequence[Tuple[str, str]]] = None,
    bar: Optional[Tuple[float, float]] = None,
    bar_style: str = "outline",
    rows: Optional[Iterable[discord.ui.Item]] = None,
    actions: Optional[discord.ui.ActionRow] = None,
    footer_note: str = "",
    accent: Optional[int] = None,
    with_dot: bool = False,
) -> discord.ui.Container:
    """
    Container im HUD-Stil: Titel, Kennzahlen-Kopf, Balken, Zeilen, Aktionen.

    Args:
        title: Überschrift, üblicherweise in Großbuchstaben.
        state: Zustand — bestimmt die Akzentfarbe (und den Punkt, falls ``with_dot``).
        subtitle: kleine Zeile unter dem Titel.
        meta: Kennzahlen als ``[(Wert, Beschriftung), …]``.
        bar: ``(Wert, Maximum)`` für den Fortschrittsbalken.
        rows: fertige Zeilen, am besten über :func:`panel_row` gebaut.
        actions: Aktionszeile über :func:`action_row`.
        footer_note: kleine Schlusszeile, z.B. „aktualisiert vor 30 Sekunden".
        accent: Akzentfarbe überschreiben (sonst aus ``state``).
        with_dot: Statuspunkt vor den Titel setzen (bei Alarmen sinnvoll).
    """
    container = discord.ui.Container(
        accent_colour=discord.Colour(accent if accent is not None else state_color(state))
    )
    container.add_item(
        discord.ui.TextDisplay(
            heading(state_title(state, title) if with_dot else title, 2)
        )
    )

    kopf: List[str] = []
    if subtitle:
        kopf.append(subtext(subtitle))
    if meta:
        kopf.append(meta_row(meta))
    if bar:
        kopf.append(progress_bar(bar[0], bar[1], 10, bar_style))
    if kopf:
        container.add_item(discord.ui.TextDisplay("\n".join(kopf)))

    zeilen = list(rows or [])
    if zeilen:
        container.add_item(discord.ui.Separator())
        for zeile in zeilen:
            container.add_item(zeile)

    if actions is not None:
        container.add_item(discord.ui.Separator())
        container.add_item(actions)

    if footer_note:
        container.add_item(discord.ui.TextDisplay(subtext(footer_note)))

    return container


def hud_panel(title: str, **kwargs) -> discord.ui.LayoutView:
    """Fertige View mit genau einem HUD-Container. Argumente wie :func:`hud_container`."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(hud_container(title, **kwargs))
    return view


def row_budget(fixed_components: int, per_row: int = 3) -> int:
    """
    Wie viele Zeilen ins 40-Komponenten-Budget passen.

    ``fixed_components`` ist alles, was unabhängig von den Zeilen im Panel steht
    (Container, Überschrift, Kopf, Trenner, Aktionszeile inklusive Buttons).
    Rechnen statt raten: sonst verschluckt ein neues Element still Zeilen.
    """
    return max((COMPONENT_LIMIT - fixed_components) // per_row, 0)


__all__ = [
    "COLOR_BRAND",
    "COMPONENT_LIMIT",
    "action_row",
    "hud_container",
    "hud_panel",
    "panel_row",
    "row_budget",
]
