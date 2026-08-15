#!/usr/bin/env python3
"""Test: Countdown als EIN aktualisiertes Panel (HUD-Stil).

Prueft die Zusagen des Umbaus:
  - erste Warnung postet, jede weitere editiert dieselbe Nachricht
  - geloeschtes Panel wird neu gepostet statt still zu verstummen
  - Abbruch und Ausfuehrung faerben das Panel um
  - der MC-Timer nennt seinen eigenen Abbruch-Befehl
"""
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord  # noqa: E402

from modules.minecraft.mc_countdown import MCCountdownTimer  # noqa: E402
from modules.restart_timer import RestartTimer  # noqa: E402

RESULTS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(ok)
    mark = "OK  " if ok else "FEHLER"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail and not ok else ""))


class FakeMessage:
    def __init__(self, kanal: "FakeChannel"):
        self.kanal = kanal
        self.embed = None
        self.edits = 0
        self.geloescht = False

    async def edit(self, embed=None, **_):
        if self.geloescht:
            raise discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"), "unknown message"
            )
        self.edits += 1
        self.embed = embed


class FakeChannel:
    def __init__(self):
        self.sends = 0
        self.messages = []

    async def send(self, embed=None, **_):
        self.sends += 1
        msg = FakeMessage(self)
        msg.embed = embed
        self.messages.append(msg)
        return msg


def _timer(kanal, minuten: int = 10, name: str = "Restart"):
    timer = RestartTimer(channel=kanal)
    timer._action_name = name
    timer._total_seconds = minuten * 60
    timer._deadline = datetime.now() + timedelta(minutes=minuten)
    return timer


async def test_ein_panel() -> None:
    print("\nEin Panel statt vier Nachrichten")
    kanal = FakeChannel()
    timer = _timer(kanal)

    await timer._update_panel("Restart in 10 Minuten!", is_initial=True)
    check("erste Warnung postet", kanal.sends == 1)

    await timer._update_panel("Restart in 5 Minuten")
    await timer._update_panel("Restart in 1 Minute!")
    check("weitere Warnungen editieren", kanal.sends == 1, f"{kanal.sends} Nachrichten")
    check("zwei Edits angekommen", kanal.messages[0].edits == 2)

    titel = kanal.messages[0].embed.title
    check("Titel im HUD-Stil", "RESTART GEPLANT" in titel, titel)
    check("Statuspunkt vorne", titel.startswith("🟡"), titel)
    beschreibung = kanal.messages[0].embed.description or ""
    check("Balken im Panel", "▬" in beschreibung or "▭" in beschreibung)
    check("relative Endzeit", "<t:" in beschreibung and ":R>" in beschreibung)


async def test_panel_geloescht() -> None:
    print("\nGeloeschtes Panel")
    kanal = FakeChannel()
    timer = _timer(kanal)

    await timer._update_panel("Restart in 10 Minuten!", is_initial=True)
    kanal.messages[0].geloescht = True
    await timer._update_panel("Restart in 5 Minuten")
    check("neu gepostet statt verstummt", kanal.sends == 2, f"{kanal.sends} Nachrichten")


async def test_zustaende() -> None:
    print("\nZustandswechsel")
    kanal = FakeChannel()
    timer = _timer(kanal)
    await timer._update_panel("Restart in 10 Minuten!", is_initial=True)

    await timer._update_panel("Server wird jetzt neu gestartet", is_final=True)
    titel = kanal.messages[0].embed.title
    check("Ausfuehrung faerbt rot", titel.startswith("🔴"), titel)
    check("Ausfuehrung im Titel", "RESTART LÄUFT" in titel, titel)

    await timer._update_panel("Restart wurde abgebrochen.", cancelled=True)
    titel = kanal.messages[0].embed.title
    check("Abbruch wird neutral", titel.startswith("⚪"), titel)
    check("Abbruch im Titel", "ABGEBROCHEN" in titel, titel)
    check("kein zweites Panel", kanal.sends == 1, f"{kanal.sends} Nachrichten")


async def test_mc_hinweis() -> None:
    print("\nMinecraft-Timer nennt seinen eigenen Abbruch-Befehl")
    kanal = FakeChannel()
    timer = MCCountdownTimer(mc_server=None, channel=kanal, extra_info="Update auf v48.5")
    timer._action_name = "Update"
    timer._total_seconds = 600
    timer._deadline = datetime.now() + timedelta(minutes=10)

    await timer._update_panel("Update in 10 Minuten!", is_initial=True)
    beschreibung = kanal.messages[0].embed.description or ""
    check("MC-Abbruchbefehl steht drin", "/modpack cancel" in beschreibung)
    check("kein falscher SAT-Befehl", "/sat cancel" not in beschreibung)
    check("Zusatzinfo uebernommen", "v48.5" in beschreibung)

    await timer._update_panel("Update laeuft", is_final=True)
    beschreibung = kanal.messages[0].embed.description or ""
    check("Ausfuehrung ohne Abbruch-Hinweis", "/modpack cancel" not in beschreibung)


async def main() -> int:
    print("=" * 62)
    print("  Countdown-Panel (HUD)")
    print("=" * 62)
    await test_ein_panel()
    await test_panel_geloescht()
    await test_zustaende()
    await test_mc_hinweis()

    print()
    if all(RESULTS):
        print(f"  ERGEBNIS: BESTANDEN ({len(RESULTS)} Checks)")
        return 0
    print(f"  ERGEBNIS: FEHLGESCHLAGEN ({RESULTS.count(False)}/{len(RESULTS)})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
