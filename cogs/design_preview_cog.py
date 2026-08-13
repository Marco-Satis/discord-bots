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
    chip,
    heading,
    meta_row,
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


# ======================================================================
# Auspraegungen der HUD-Sprache (Marcos Wahl) — A bis E
# ======================================================================


def _todo_zeile(t: Dict[str, Any], meta: bool = True) -> discord.ui.Section:
    """Eine Eintragszeile im HUD-Stil: Text links, Checkbox rechts."""
    kopf = f"~~{t['text']}~~" if t["done"] else f"**{t['text']}**"
    body = kopf + ("\n" + subtext(t["wer"]) if meta else "")
    return discord.ui.Section(
        discord.ui.TextDisplay(body),
        accessory=InertButton(
            emoji="✅" if t["done"] else "⬜",
            style=discord.ButtonStyle.success if t["done"] else discord.ButtonStyle.secondary,
        ),
    )


def hud_b() -> discord.ui.LayoutView:
    """Kennzahlen-Kopf — kein Emoji im Titel, drei Zahlen in einer Zeile."""
    c = discord.ui.Container(accent_colour=discord.Colour(C_HUD))
    c.add_item(discord.ui.TextDisplay(heading("BAU-ZIELE", 2)))
    c.add_item(
        discord.ui.TextDisplay(
            meta_row(
                [
                    (str(len(OFFEN)), "offen"),
                    (str(len(FERTIG)), "erledigt"),
                    (f"{int(len(FERTIG) / len(DEMO_TODOS) * 100)}%", "fertig"),
                ]
            )
            + "\n"
            + progress_bar(len(FERTIG), len(DEMO_TODOS), 10, "outline")
        )
    )
    c.add_item(discord.ui.Separator())
    for t in DEMO_TODOS:
        c.add_item(_todo_zeile(t))
    c.add_item(discord.ui.Separator())
    row = discord.ui.ActionRow()
    row.add_item(InertButton(label="+ Eintrag", style=discord.ButtonStyle.primary))
    row.add_item(InertButton(label="Aufraeumen", style=discord.ButtonStyle.secondary))
    c.add_item(row)
    return _demo_view(c)


def hud_c() -> discord.ui.LayoutView:
    """Gruppiert — Zwischenueberschriften mit Zaehler, skaliert bei langen Listen."""
    c = discord.ui.Container(accent_colour=discord.Colour(C_HUD))
    c.add_item(
        discord.ui.Section(
            discord.ui.TextDisplay(
                heading("BAU-ZIELE", 2) + "\n" + subtext("Satisfactory · Ostfront")
            ),
            accessory=InertButton(label=f"{int(len(FERTIG) / len(DEMO_TODOS) * 100)}%"),
        )
    )
    c.add_item(
        discord.ui.TextDisplay(progress_bar(len(FERTIG), len(DEMO_TODOS), 10, "outline"))
    )
    c.add_item(discord.ui.Separator())
    c.add_item(discord.ui.TextDisplay(subtext(f"OFFEN · {len(OFFEN)}")))
    for t in OFFEN:
        c.add_item(_todo_zeile(t))
    c.add_item(discord.ui.Separator())
    c.add_item(discord.ui.TextDisplay(subtext(f"ERLEDIGT · {len(FERTIG)}")))
    for t in FERTIG:
        c.add_item(_todo_zeile(t))
    return _demo_view(c)


def hud_d() -> discord.ui.LayoutView:
    """Dicht — eine Zeile pro Eintrag, Meta als Chip, feiner Balken im Kopf."""
    c = discord.ui.Container(accent_colour=discord.Colour(C_HUD))
    c.add_item(
        discord.ui.TextDisplay(
            heading("BAU-ZIELE", 3)
            + "  "
            + subtext(
                progress_bar(len(FERTIG), len(DEMO_TODOS), 9, "line")
                + f" {int(len(FERTIG) / len(DEMO_TODOS) * 100)}%"
            )
        )
    )
    c.add_item(discord.ui.Separator())
    for t in DEMO_TODOS:
        text = f"~~{t['text']}~~" if t["done"] else f"**{t['text']}** {chip(t['wer'])}"
        c.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(text),
                accessory=InertButton(
                    emoji="✅" if t["done"] else "⬜",
                    style=discord.ButtonStyle.success
                    if t["done"]
                    else discord.ButtonStyle.secondary,
                ),
            )
        )
    return _demo_view(c)


def hud_e(icon_url: str = "") -> discord.ui.LayoutView:
    """Icon-Kopf — Bildkachel links, Titel und Kennzahlen rechts."""
    c = discord.ui.Container(accent_colour=discord.Colour(C_HUD))
    kopf = (
        heading("BAU-ZIELE", 3)
        + "\n"
        + subtext(
            f"{len(OFFEN)} offen · {len(FERTIG)} erledigt · "
            f"zuletzt {rel_time(datetime.now() - timedelta(minutes=12))}"
        )
        + "\n"
        + progress_bar(len(FERTIG), len(DEMO_TODOS), 10, "outline")
    )
    if icon_url:
        # Thumbnail als Accessory — die einzige Bildkachel, die Discord neben
        # Text erlaubt.
        c.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(kopf),
                accessory=discord.ui.Thumbnail(media=icon_url),
            )
        )
    else:
        c.add_item(discord.ui.TextDisplay(kopf))
    c.add_item(discord.ui.Separator())
    for t in DEMO_TODOS:
        c.add_item(_todo_zeile(t))
    return _demo_view(c)


HUD_VARIANTEN = {
    "a": ("A · Pur (deine Wahl)", v_hud),
    "b": ("B · Kennzahlen-Kopf", hud_b),
    "c": ("C · Gruppiert", hud_c),
    "d": ("D · Dicht", hud_d),
    "e": ("E · Icon-Kopf", hud_e),
}


# ======================================================================
# Beispiel-Panels — dieselbe Sprache auf echte Bot-Funktionen
# ======================================================================


def bsp_serverstatus() -> discord.ui.LayoutView:
    c = discord.ui.Container(accent_colour=discord.Colour(C_HUD))
    c.add_item(discord.ui.TextDisplay(heading("SERVERSTATUS", 2)))
    c.add_item(discord.ui.TextDisplay(subtext("3 Server · 4 Spieler online")))
    c.add_item(discord.ui.Separator())
    for name, state, spieler, maxs, extra, aktion in (
        ("Satisfactory", "ok", 3, 8, "Uptime 4d 2h", ("Neustart", discord.ButtonStyle.secondary)),
        ("Minecraft BMC5", "ok", 1, 20, "Uptime 12h", ("Neustart", discord.ButtonStyle.secondary)),
        ("Minecraft Vanilla", "off", 0, 20, "gestoppt", ("Starten", discord.ButtonStyle.success)),
    ):
        c.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    f"{status_dot(state)} **{name}** · {capacity(spieler, maxs)}\n"
                    f"{progress_bar(spieler, maxs, 10)} {subtext(extra)}"
                ),
                accessory=InertButton(label=aktion[0], style=aktion[1]),
            )
        )
    c.add_item(discord.ui.Separator())
    c.add_item(
        discord.ui.TextDisplay(
            subtext(f"aktualisiert {rel_time(datetime.now() - timedelta(seconds=30))}")
        )
    )
    return _demo_view(c)


def bsp_neustart() -> discord.ui.LayoutView:
    c = discord.ui.Container(accent_colour=discord.Colour(0xE5484D))
    c.add_item(discord.ui.TextDisplay(heading("NEUSTART GEPLANT", 2)))
    c.add_item(
        discord.ui.TextDisplay(
            progress_bar(7, 10, 10, "outline")
            + f"\n{status_dot('crit')} **{rel_time(datetime.now() + timedelta(minutes=7))}** "
            + subtext("· 03:00 Uhr")
            + "\n"
            + subtext("Satisfactory · Speichern läuft automatisch · 3 Spieler online")
        )
    )
    c.add_item(discord.ui.Separator())
    row = discord.ui.ActionRow()
    row.add_item(InertButton(label="Jetzt neu starten", style=discord.ButtonStyle.danger))
    row.add_item(InertButton(label="+15 Minuten"))
    row.add_item(InertButton(label="Abbrechen"))
    c.add_item(row)
    return _demo_view(c)


def bsp_backup() -> discord.ui.LayoutView:
    c = discord.ui.Container(accent_colour=discord.Colour(0x2ECC71))
    c.add_item(
        discord.ui.TextDisplay(
            heading("💾 BACKUP", 3)
            + "\n"
            + subtext(f"letzter Lauf {rel_time(datetime.now() - timedelta(hours=4))} · 1,2 GB")
            + "\n"
            + meta_row([("28", "gespeichert"), ("0", "Fehler"), ("34 GB", "belegt")])
            + "\n"
            + progress_bar(30, 30, 10)
            + " "
            + subtext("30/30 erfolgreich")
        )
    )
    c.add_item(discord.ui.Separator())
    row = discord.ui.ActionRow()
    row.add_item(InertButton(label="Jetzt sichern", style=discord.ButtonStyle.primary))
    row.add_item(InertButton(label="Liste"))
    c.add_item(row)
    return _demo_view(c)


def bsp_mods() -> discord.ui.LayoutView:
    c = discord.ui.Container(accent_colour=discord.Colour(C_PANEL))
    c.add_item(discord.ui.TextDisplay(heading("MOD-UPDATES", 2)))
    c.add_item(discord.ui.TextDisplay(subtext("Satisfactory · 3 von 24 Mods veraltet")))
    c.add_item(discord.ui.Separator())
    for name, alt, neu, hinweis in (
        ("Ficsit Remote Monitoring", "1.4.2", "1.5.0", ""),
        ("Structural Solutions", "2.1.0", "2.2.1", ""),
        ("Refined Power", "", "", "⚠ benötigt Spielversion 1.1"),
    ):
        zeile = f"**{name}**\n" + subtext(
            hinweis if hinweis else f"{chip(alt)} → {chip(neu)}"
        )
        c.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(zeile),
                accessory=InertButton(
                    label="Details" if hinweis else "Update",
                    style=discord.ButtonStyle.secondary
                    if hinweis
                    else discord.ButtonStyle.primary,
                ),
            )
        )
    c.add_item(discord.ui.Separator())
    row = discord.ui.ActionRow()
    row.add_item(InertButton(label="Alle aktualisieren", style=discord.ButtonStyle.success))
    c.add_item(row)
    return _demo_view(c)


def bsp_levelup() -> discord.ui.LayoutView:
    c = discord.ui.Container(accent_colour=discord.Colour(C_NEON))
    c.add_item(
        discord.ui.TextDisplay(
            heading("🏆 Level 12 erreicht", 3)
            + "\n"
            + subtext("Marco · Rang 4 von 38")
            + "\n"
            + progress_bar(340, 1100, 10)
            + " "
            + subtext("340 / 1100 XP bis Level 13")
            + "\n"
            + meta_row([("12.480", "XP gesamt"), ("+55", "heute")])
        )
    )
    return _demo_view(c)


def bsp_giveaway() -> discord.ui.LayoutView:
    c = discord.ui.Container(accent_colour=discord.Colour(C_HUD))
    c.add_item(discord.ui.TextDisplay(heading("🎁 GIVEAWAY", 2)))
    c.add_item(
        discord.ui.TextDisplay(
            "**Satisfactory Steam-Key**\n"
            + subtext(f"endet {rel_time(datetime.now() + timedelta(hours=2))} · 1 Gewinner")
            + "\n"
            + progress_bar(14, 25, 10, "outline")
            + " "
            + capacity(14, 25)
        )
    )
    c.add_item(discord.ui.Separator())
    row = discord.ui.ActionRow()
    row.add_item(InertButton(label="Teilnehmen", style=discord.ButtonStyle.primary))
    row.add_item(InertButton(label="Teilnehmer"))
    c.add_item(row)
    c.add_item(
        discord.ui.TextDisplay(
            subtext(f"von Marco · gestartet {rel_time(datetime.now() - timedelta(hours=22))}")
        )
    )
    return _demo_view(c)


def bsp_ticket() -> discord.ui.LayoutView:
    c = discord.ui.Container(accent_colour=discord.Colour(C_PANEL))
    c.add_item(
        discord.ui.Section(
            discord.ui.TextDisplay(
                heading("TICKET #148", 3)
                + "\n"
                + subtext("Serverabsturz beim Zugbau · von Lena")
            ),
            accessory=InertButton(label="offen"),
        )
    )
    c.add_item(
        discord.ui.TextDisplay(
            f"{status_dot('warn')} **wartet** auf Antwort · seit **3 Stunden** · "
            f"zuständig **Marco**"
        )
    )
    c.add_item(discord.ui.Separator())
    row = discord.ui.ActionRow()
    row.add_item(InertButton(label="Übernehmen", style=discord.ButtonStyle.primary))
    row.add_item(InertButton(label="Schließen"))
    row.add_item(InertButton(label="Transkript"))
    c.add_item(row)
    return _demo_view(c)


def bsp_moderation() -> discord.ui.LayoutView:
    c = discord.ui.Container(accent_colour=discord.Colour(0xE5484D))
    c.add_item(discord.ui.TextDisplay(heading("VERWARNUNG", 3)))
    c.add_item(
        discord.ui.TextDisplay(
            "**@Spieler123** " + subtext("· Werbung im Chat") + "\n"
            + progress_bar(2, 3, 3)
            + " "
            + subtext("2 von 3 Verwarnungen · nächste bedeutet Bann")
            + "\n"
            + subtext("durch Marco · vor 1 Minute · Fall #77")
        )
    )
    c.add_item(discord.ui.Separator())
    row = discord.ui.ActionRow()
    row.add_item(InertButton(label="Aufheben"))
    row.add_item(InertButton(label="Bannen", style=discord.ButtonStyle.danger))
    c.add_item(row)
    return _demo_view(c)


def bsp_alarm() -> discord.ui.LayoutView:
    """Alarm und Entwarnung — dasselbe Panel, nur umgefaerbt statt zweiter Nachricht."""
    view = discord.ui.LayoutView(timeout=900)

    alarm = discord.ui.Container(accent_colour=discord.Colour(0xE5484D))
    alarm.add_item(discord.ui.TextDisplay(heading("🔴 ALARM · RAM", 3)))
    alarm.add_item(
        discord.ui.TextDisplay(
            f"{progress_bar(92, 100, 10)} **92%** {subtext('von 8 GB')}\n"
            + subtext("Schwelle 85% seit 6 Minuten überschritten · Minecraft BMC5")
        )
    )
    alarm.add_item(discord.ui.Separator())
    row = discord.ui.ActionRow()
    row.add_item(InertButton(label="Stumm 1h"))
    row.add_item(InertButton(label="Server neu starten", style=discord.ButtonStyle.danger))
    alarm.add_item(row)
    view.add_item(alarm)

    ok = discord.ui.Container(accent_colour=discord.Colour(0x2ECC71))
    ok.add_item(discord.ui.TextDisplay(heading("🟢 ENTWARNUNG · RAM", 3)))
    ok.add_item(
        discord.ui.TextDisplay(
            f"{progress_bar(54, 100, 10)} **54%** {subtext('von 8 GB')}\n"
            + subtext("wieder im grünen Bereich · Dauer des Vorfalls 14 Minuten")
        )
    )
    view.add_item(ok)
    return view


BEISPIELE = {
    "serverstatus": ("Serverstatus (mehrere Server)", bsp_serverstatus),
    "neustart": ("Neustart-Countdown", bsp_neustart),
    "backup": ("Backup-Status", bsp_backup),
    "mods": ("Mod-Updates", bsp_mods),
    "levelup": ("Level-Up", bsp_levelup),
    "giveaway": ("Giveaway", bsp_giveaway),
    "ticket": ("Ticket", bsp_ticket),
    "moderation": ("Verwarnung", bsp_moderation),
    "alarm": ("Monitoring-Alarm + Entwarnung", bsp_alarm),
}


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

    @design.command(name="hud", description="HUD-Auspraegungen A-E des To-Do-Boards")
    @app_commands.describe(variante="Welche Auspraegung?")
    @app_commands.choices(
        variante=[
            app_commands.Choice(name=label, value=key)
            for key, (label, _) in HUD_VARIANTEN.items()
        ]
    )
    @admin_only()
    async def design_hud(
        self, interaction: discord.Interaction, variante: app_commands.Choice[str]
    ) -> None:
        _, builder = HUD_VARIANTEN[variante.value]
        if variante.value == "e":
            # Variante E lebt von der Bildkachel — Bot-Avatar als Platzhalter.
            icon = interaction.client.user.display_avatar.url if interaction.client.user else ""
            view = builder(icon)
        else:
            view = builder()
        await interaction.response.send_message(view=view, ephemeral=True)

    @design.command(name="beispiel", description="HUD-Sprache auf andere Bot-Funktionen")
    @app_commands.describe(typ="Welches Panel?")
    @app_commands.choices(
        typ=[
            app_commands.Choice(name=label, value=key)
            for key, (label, _) in BEISPIELE.items()
        ]
    )
    @admin_only()
    async def design_beispiel(
        self, interaction: discord.Interaction, typ: app_commands.Choice[str]
    ) -> None:
        _, builder = BEISPIELE[typ.value]
        await interaction.response.send_message(view=builder(), ephemeral=True)

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
