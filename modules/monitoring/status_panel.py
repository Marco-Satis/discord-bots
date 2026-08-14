"""
Rendering des Live-Status-Panels — getrennt von der Datenbeschaffung.

Der Bot sammelt die Werte (systemd, API, RCON, Savegame) und uebergibt sie hier
als einfache Datensaetze. Dadurch laesst sich das Panel ohne laufenden Server
testen; vorher steckte das Rendern mitten in einer 200-Zeilen-Funktion in
``bots/recon_bot.py`` und war nur am lebenden System pruefbar.

Stil: HUD (siehe docs/DESIGN_HUD.md) — Kennzahlen-Kopf, je Server eine Zeile mit
Statuspunkt, Auslastung und Balken, Details als Subtext.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import discord

from utils.embeds import hud_embed, status_field
from utils.ui_kit import subtext, truncate

# Discord nimmt 4096 Zeichen Beschreibung. Bei vielen oder langen Spielernamen
# kommt das Panel dem nahe — ein HTTP 400 beim Edit wuerde es einfrieren.
MAX_BESCHREIBUNG = 4000


@dataclass
class ServerLine:
    """Ein Server im Panel."""

    name: str
    online: bool
    players: Optional[int] = None
    limit: Optional[int] = None
    note: str = ""
    # Zusatzzeilen (Spielerliste, Weltgroesse, Neustart, Updates) — werden als
    # Subtext unter die Serverzeile gehaengt.
    details: List[str] = field(default_factory=list)
    # True, wenn die Namensliste weniger Spieler kennt als der Zaehler sagt.
    # Ohne diese Kennzeichnung widerspricht sich das Panel sichtbar ("2 Spieler",
    # ein Name) und niemand weiss, welcher Wert stimmt. Die Zahl fuehrt.
    liste_unvollstaendig: bool = False

    def render(self) -> str:
        zeilen = [
            status_field(
                self.name,
                "ok" if self.online else "off",
                value=self.players if self.online else None,
                maximum=self.limit if self.online else None,
                note=self.note or ("" if self.online else "gestoppt"),
            )
        ]
        zeilen.extend(subtext(d) for d in self.details if d)
        if self.liste_unvollstaendig:
            zeilen.append(subtext("Namensliste unvollständig — die Zahl führt"))
        return "\n".join(zeilen)


def build_status_panel(
    server: Sequence[ServerLine],
    *,
    footer: str = "Aktualisiert alle 5 Minuten",
) -> discord.Embed:
    """
    Panel aus den Serverzeilen bauen.

    Kopfzahlen (Spieler gesamt, wie viele Server laufen) werden aus den Zeilen
    abgeleitet — so koennen Kopf und Inhalt nicht auseinanderlaufen.
    """
    spieler = sum(s.players or 0 for s in server if s.online)
    laufen = sum(1 for s in server if s.online)

    # Leerzeile zwischen den Servern — ohne sie laufen die Bloecke auf dem Handy
    # zu einer Wand zusammen.
    zeilen: List[str] = []
    for eintrag in server:
        if zeilen:
            zeilen.append("")
        zeilen.append(eintrag.render())

    embed = hud_embed(
        "SERVERSTATUS",
        state="ok" if laufen else "off",
        meta=[
            (str(spieler), "Spieler"),
            (f"{laufen}/{len(server)}", "Server"),
        ],
        lines=zeilen,
        footer=footer,
    )
    if embed.description and len(embed.description) > MAX_BESCHREIBUNG:
        embed.description = truncate(embed.description, MAX_BESCHREIBUNG)
    return embed
