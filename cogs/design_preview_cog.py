"""
Design-Vorschau (/design) — Stil-Varianten zum Anschauen, bevor umgebaut wird.

Alle Antworten sind ephemeral: nur der Aufrufer sieht sie, der Kanal bleibt
sauber. Der Cog aendert NICHTS am Produktiv-Verhalten, er rendert nur
Beispiel-Daten in verschiedenen Design-Sprachen:

    /design board <variante>   — dasselbe To-Do-Board in 6 Stilen
    /design meldungen          — Erfolg/Fehler/Warnung/Info im neuen Stil
    /design vergleich          — Server-Status alt (Standard-Embed) vs. neu

Sobald eine Variante gewinnt, wandert deren Formsprache nach utils/embeds.py +
utils/ui_kit.py und gilt fuer alle Bots.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

import discord
from discord import app_commands
from discord.ext import commands

from utils import admin_only, get_logger
from utils.ui_kit import (
    RULE_THIN,
    capacity,
    heading,
    progress_bar,
    rel_time,
    status_dot,
    subtext,
    truncate,
)

logger = get_logger("cogs.design")

# Beispiel-Daten — bewusst realistisch, damit die Varianten vergleichbar sind.
DEMO_TODOS: List[Dict[str, Any]] = [
    {"id": 1, "text": "Kohlekraftwerk Sued erweitern", "done": False, "wer": "Marco"},
    {"id": 2, "text": "Zugstrecke zur Oelquelle", "done": False, "wer": "Lena"},
    {"id": 3, "text": "Stahltraeger-Fabrik automatisieren", "done": False, "wer": "Tom"},
    {"id": 4, "text": "Bauxit-Aussenposten sichern", "done": True, "wer": "Marco"},
    {"id": 5, "text": "Lagerhalle sortieren", "done": True, "wer": "Lena"},
]

OFFEN = [t for t in DEMO_TODOS if not t["done"]]
FERTIG = [t for t in DEMO_TODOS if t["done"]]

# Farben der Varianten — bewusst unterschiedlich, damit der Unterschied auffaellt.
C_HUD = 0xF2C14E       # warmes Gold wie im Referenz-Panel
C_KARTE = 0x2B9348     # Satisfactory-Gruen
C_NEON = 0x7C5CFF      # kraeftiges Violett
C_PANEL = 0x1F6FEB     # technisches Blau
C_MINIMAL = 0x2B2D31   # Discord-Hintergrund -> Rand verschwindet fast


class InertButton(discord.ui.Button):
    """Button, der in der Vorschau bewusst nichts tut."""

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()


def _demo_view(container: discord.ui.Container) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=900)
    view.add_item(container)
    return view


# ======================================================================
# Variante 1 — HUD (Formsprache aus Marcos Referenz-Screenshot)
# ======================================================================


def v_hud() -> discord.ui.LayoutView:
    c = discord.ui.Container(accent_colour=discord.Colour(C_HUD))
    c.add_item(discord.ui.TextDisplay(heading("💛 BAU-ZIELE", 2)))
    c.add_item(
        discord.ui.TextDisplay(
            f"{progress_bar(len(FERTIG), len(DEMO_TODOS), 10, 'outline')} "
            f"{capacity(len(FERTIG), len(DEMO_TODOS), '✅')}\n"
            f"{status_dot('warn')} In Arbeit · {len(OFFEN)} offen"
        )
    )
    row = discord.ui.ActionRow()
    row.add_item(InertButton(label="+ Eintrag", style=discord.ButtonStyle.secondary))
    row.add_item(InertButton(label="Erledigte ausblenden", style=discord.ButtonStyle.secondary))
    c.add_item(row)
    c.add_item(discord.ui.Separator())
    for t in DEMO_TODOS:
        meta = subtext("#{} · {}".format(t["id"], t["wer"]))
        c.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    (f"~~{t['text']}~~" if t["done"] else f"**{t['text']}**")
                    + "\n"
                    + meta
                ),
                accessory=InertButton(
                    emoji="✅" if t["done"] else "⬜",
                    style=discord.ButtonStyle.success
                    if t["done"]
                    else discord.ButtonStyle.secondary,
                ),
            )
        )
    c.add_item(discord.ui.Separator())
    c.add_item(
        discord.ui.TextDisplay(subtext("Tippe die Box neben einem Eintrag zum Abhaken."))
    )
    return _demo_view(c)


# ======================================================================
# Variante 2 — Karte (ruhig, textzentriert, wenig Deko)
# ======================================================================


def v_karte() -> discord.ui.LayoutView:
    c = discord.ui.Container(accent_colour=discord.Colour(C_KARTE))
    c.add_item(discord.ui.TextDisplay(heading("To-Do — Satisfactory", 2)))
    c.add_item(
        discord.ui.TextDisplay(
            subtext(
                f"{len(OFFEN)} offen · {len(FERTIG)} erledigt · "
                f"zuletzt {rel_time(datetime.now() - timedelta(minutes=12))}"
            )
        )
    )
    c.add_item(discord.ui.Separator())
    for t in OFFEN:
        c.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(f"**{t['text']}**\n{subtext(t['wer'])}"),
                accessory=InertButton(emoji="⬜", style=discord.ButtonStyle.secondary),
            )
        )
    c.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
    c.add_item(discord.ui.TextDisplay(subtext("ERLEDIGT")))
    for t in FERTIG:
        c.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(f"~~{t['text']}~~\n{subtext(t['wer'])}"),
                accessory=InertButton(emoji="✅", style=discord.ButtonStyle.success),
            )
        )
    return _demo_view(c)


# ======================================================================
# Variante 3 — Neon (kraeftiger Akzent, Blockbalken, hoher Kontrast)
# ======================================================================


def v_neon() -> discord.ui.LayoutView:
    c = discord.ui.Container(accent_colour=discord.Colour(C_NEON))
    c.add_item(discord.ui.TextDisplay(heading("▌ BAUPLAN", 1)))
    c.add_item(
        discord.ui.TextDisplay(
            f"`{progress_bar(len(FERTIG), len(DEMO_TODOS), 12, 'block')}` "
            f"**{int(len(FERTIG) / len(DEMO_TODOS) * 100)}%**"
        )
    )
    c.add_item(discord.ui.Separator())
    for t in DEMO_TODOS:
        mark = "▰" if t["done"] else "▱"
        line = f"`{mark}` " + (f"~~{t['text']}~~" if t["done"] else t["text"])
        c.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(line),
                accessory=InertButton(
                    label="AUF" if t["done"] else "AB",
                    style=discord.ButtonStyle.primary
                    if not t["done"]
                    else discord.ButtonStyle.secondary,
                ),
            )
        )
    c.add_item(discord.ui.Separator())
    c.add_item(discord.ui.TextDisplay(subtext(f"aktualisiert {rel_time(datetime.now())}")))
    return _demo_view(c)


# ======================================================================
# Variante 4 — Panel (klassisches Embed, aber mit Balken + Feldern)
# ======================================================================


def v_panel() -> discord.Embed:
    e = discord.Embed(
        title="Bau-Ziele",
        description=(
            f"{progress_bar(len(FERTIG), len(DEMO_TODOS), 10)} "
            f"**{len(FERTIG)}/{len(DEMO_TODOS)}**\n"
            f"{status_dot('warn')} {len(OFFEN)} offen · "
            f"zuletzt {rel_time(datetime.now() - timedelta(minutes=12))}"
        ),
        color=C_PANEL,
    )
    e.add_field(
        name="Offen",
        value="\n".join(f"`{t['id']:>2}` {t['text']}\n{subtext(t['wer'])}" for t in OFFEN),
        inline=False,
    )
    e.add_field(
        name="Erledigt",
        value="\n".join(f"`{t['id']:>2}` ~~{t['text']}~~" for t in FERTIG),
        inline=False,
    )
    e.set_footer(text="Satisfactory · Ostfront")
    return e


# ======================================================================
# Variante 5 — Mono (Monospace-Block, tabellarisch, ohne Emoji)
# ======================================================================


def v_mono() -> discord.Embed:
    zeilen = [f"{'ST':<3}{'NR':<4}AUFGABE", "-" * 34]
    for t in DEMO_TODOS:
        zeilen.append(f"{'[x]' if t['done'] else '[ ]':<3}{t['id']:<4}{truncate(t['text'], 26)}")
    body = "\n".join(zeilen)
    e = discord.Embed(
        title="TO-DO / SATISFACTORY",
        description=f"```\n{body}\n```\n{subtext(f'{len(OFFEN)} offen · {len(FERTIG)} erledigt')}",
        color=C_MINIMAL,
    )
    return e


# ======================================================================
# Variante 6 — Minimal (fast keine Deko, alles ueber Typografie)
# ======================================================================


def v_minimal() -> discord.Embed:
    lines = []
    for t in OFFEN:
        lines.append("**{}**\n{}".format(t["text"], subtext("{} · #{}".format(t["wer"], t["id"]))))
    lines.append(RULE_THIN)
    for t in FERTIG:
        lines.append("~~{}~~\n{}".format(t["text"], subtext("{} · #{}".format(t["wer"], t["id"]))))
    e = discord.Embed(
        description=f"{heading('Bau-Ziele', 3)}\n\n" + "\n\n".join(lines),
        color=C_MINIMAL,
    )
    e.set_footer(text=f"{len(OFFEN)} offen · {len(FERTIG)} erledigt")
    return e


VARIANTEN = {
    "hud": ("HUD — Formsprache deines Referenz-Panels", v_hud),
    "karte": ("Karte — ruhig, textzentriert", v_karte),
    "neon": ("Neon — kraeftiger Akzent, Blockbalken", v_neon),
    "panel": ("Panel — klassisches Embed mit Balken + Feldern", v_panel),
    "mono": ("Mono — Monospace-Tabelle, keine Emoji", v_mono),
    "minimal": ("Minimal — nur Typografie", v_minimal),
}


# ======================================================================
# Meldungs-Stile (Erfolg/Fehler/Warnung/Info)
# ======================================================================


def meldungs_view() -> discord.ui.LayoutView:
    """Die vier Standard-Meldungen in der neuen Formsprache."""
    view = discord.ui.LayoutView(timeout=900)
    for farbe, dot, titel, text in (
        (0x2ECC71, "ok", "Server gestartet", "Satisfactory laeuft wieder · 12s Startzeit"),
        (0xE5484D, "crit", "RCON nicht erreichbar", "3 Versuche gescheitert · Port 15777"),
        (0xF2C14E, "warn", "Speicher knapp", "RAM 87% · Backup verschoben"),
        (0x1F6FEB, "info", "Wartung geplant", "Sonntag 03:00 · ca. 20 Minuten"),
    ):
        c = discord.ui.Container(accent_colour=discord.Colour(farbe))
        c.add_item(
            discord.ui.TextDisplay(
                f"{status_dot(dot)} **{titel}**\n{subtext(text)}\n"
                f"{subtext(rel_time(datetime.now()))}"
            )
        )
        view.add_item(c)
    return view


def vergleich_alt() -> discord.Embed:
    """Server-Status im heutigen Standard-Stil."""
    e = discord.Embed(
        title="Server-Status",
        description="Status der Gameserver",
        color=0x5865F2,
        timestamp=datetime.now(),
    )
    e.add_field(name="Satisfactory", value="Online\n3/8 Spieler", inline=True)
    e.add_field(name="Minecraft BMC5", value="Online\n1/20 Spieler", inline=True)
    e.add_field(name="Minecraft Vanilla", value="Offline", inline=True)
    e.set_footer(text="Discord Bots")
    return e


def vergleich_neu() -> discord.ui.LayoutView:
    """Derselbe Inhalt in der neuen Formsprache."""
    c = discord.ui.Container(accent_colour=discord.Colour(C_HUD))
    c.add_item(discord.ui.TextDisplay(heading("SERVERSTATUS", 2)))
    for name, state, spieler, maxs, extra in (
        ("Satisfactory", "ok", 3, 8, "Uptime 4d 2h"),
        ("Minecraft BMC5", "ok", 1, 20, "Uptime 12h"),
        ("Minecraft Vanilla", "off", 0, 20, "gestoppt"),
    ):
        c.add_item(
            discord.ui.TextDisplay(
                f"{status_dot(state)} **{name}** · {capacity(spieler, maxs)}\n"
                f"`{progress_bar(spieler, maxs, 10)}` {subtext(extra)}"
            )
        )
    c.add_item(discord.ui.Separator())
    c.add_item(discord.ui.TextDisplay(subtext(f"aktualisiert {rel_time(datetime.now())}")))
    return _demo_view(c)


class DesignPreviewCog(commands.Cog, name="Design"):
    """Vorschau der Embed-/Panel-Stile — reine Anschauung, ohne Nebenwirkung."""

    design = app_commands.Group(name="design", description="Design-Vorschau")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        logger.info("DesignPreviewCog geladen — /design registriert")

    @design.command(name="board", description="To-Do-Board in einer Stil-Variante zeigen")
    @app_commands.describe(variante="Welche Design-Sprache?")
    @app_commands.choices(
        variante=[
            app_commands.Choice(name=label, value=key)
            for key, (label, _) in VARIANTEN.items()
        ]
    )
    @admin_only()
    async def design_board(
        self, interaction: discord.Interaction, variante: app_commands.Choice[str]
    ) -> None:
        _, builder = VARIANTEN[variante.value]
        result = builder()
        if isinstance(result, discord.ui.LayoutView):
            await interaction.response.send_message(view=result, ephemeral=True)
        else:
            await interaction.response.send_message(embed=result, ephemeral=True)

    @design.command(name="meldungen", description="Erfolg/Fehler/Warnung/Info im neuen Stil")
    @admin_only()
    async def design_meldungen(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(view=meldungs_view(), ephemeral=True)

    @design.command(name="vergleich", description="Server-Status: heutiger Stil vs. neuer Stil")
    @admin_only()
    async def design_vergleich(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=vergleich_alt(), ephemeral=True)
        await interaction.followup.send(view=vergleich_neu(), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DesignPreviewCog(bot))
