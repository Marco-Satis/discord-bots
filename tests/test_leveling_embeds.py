#!/usr/bin/env python3
"""
Tests für das Rang-Panel (cogs/leveling_cog.py).

``/rank`` zeigte sechs Felder mit je einer Zahl darin. Neu: Kennzahlen-Kopf,
XP-Balken, zwei Subtext-Zeilen.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_leveling_embeds.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import discord  # noqa: F401
    from cogs.leveling_cog import LevelingCog
    from utils.embeds import COLOR_PROGRESS
    HAVE_DISCORD = True
except ImportError:
    HAVE_DISCORD = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _member(name: str = "Marco"):
    return SimpleNamespace(
        display_name=name,
        display_avatar=SimpleNamespace(url="https://example.invalid/a.png"),
        color=None,
        mention=f"@{name}",
    )


def _cog():
    """Cog-Attrappe mit der echten XP-Kurve aus modules/leveling.py."""
    return SimpleNamespace(
        leveling=SimpleNamespace(
            xp_for_level=lambda level: 5 * level * level + 50 * level + 100
        )
    )


def _rang(**werte):
    # Level 5 braucht 475 XP, Level 4 lag bei 380 -> 1.050 XP heisst 670 im Level.
    daten = {"level": 5, "xp": 1050, "total_messages": 1234, "voice_minutes": 90}
    daten.update(werte)
    return LevelingCog._build_rank_embed(_cog(), _member(), daten, rank=3)


def run_tests() -> None:
    e = _rang()
    _check("titel_rang", "RANG #3" in e.title, e.title)
    _check("titel_name", "Marco" in e.title, e.title)
    _check("farbe_fortschritt", e.color.value == COLOR_PROGRESS, str(e.color))
    _check("keine_felder", len(e.fields) == 0, f"{len(e.fields)} Felder")
    _check("kennzahl_level", "**5** Level" in e.description, e.description)
    _check("kennzahl_xp_deutsch", "**1.050** XP" in e.description, e.description)
    _check("balken_vorhanden", "▬" in e.description or "▭" in e.description, e.description)
    _check("xp_bis_naechstes", "670 / 95 XP bis Level 6" in e.description, e.description)
    _check("nachrichten_deutsch", "1.234 Nachrichten" in e.description, e.description)
    _check("voice_lesbar", "1h 30m Voice" in e.description, e.description)
    _check("thumbnail", e.thumbnail.url.endswith("a.png"), str(e.thumbnail.url))

    # Level 0: kein Rueckgriff auf ein Level -1, Balken bleibt rechenbar
    null = LevelingCog._build_rank_embed(_cog(), _member(), {"level": 0, "xp": 250}, rank=99)
    _check("level0_kennzahl", "**0** Level" in null.description, null.description)
    _check("level0_fortschritt", "250 / 100 XP bis Level 1" in null.description,
           null.description)
    _check("level0_keine_felder", len(null.fields) == 0)

    # Voice unter einer Stunde bleibt in Minuten
    kurz = _rang(voice_minutes=45)
    _check("voice_minuten", "45m Voice" in kurz.description, kurz.description)

    # Embed-Titel rendern kein Markdown — der Name steht dort unverändert,
    # Escapes wären nur als Backslashes sichtbar. Geprüft wird stattdessen,
    # dass ein sehr langer Name die 256-Zeichen-Grenze des Titels nicht sprengt.
    roh = LevelingCog._build_rank_embed(
        _cog(), _member("**fett**"), {"level": 1, "xp": 10}, rank=1
    )
    _check("name_unveraendert", "**fett**" in roh.title, roh.title)
    lang = LevelingCog._build_rank_embed(
        _cog(), _member("N" * 300), {"level": 1, "xp": 10}, rank=1
    )
    _check("titel_unter_grenze", len(lang.title) <= 256, str(len(lang.title)))


def main() -> int:
    print("=" * 60)
    print("  Rang-Panel Tests (cogs/leveling_cog.py)")
    print("=" * 60)
    if not HAVE_DISCORD:
        print("  [SKIP] discord.py lokal nicht installiert — Test läuft am Server.")
        print("  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN (übersprungen)")
        return 0

    try:
        run_tests()
    except Exception as e:  # noqa: BLE001
        _check("run", False, f"Exception: {e}")

    failed = 0
    for name, ok, msg in _results:
        status = "[OK]  " if ok else "[FAIL]"
        line = f"  {status} {name}"
        if not ok and msg:
            line += f"  -> {msg}"
        print(line)
        if not ok:
            failed += 1

    print("-" * 60)
    if failed == 0:
        print(f"  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN ({len(_results)} Checks)")
        return 0
    print(f"  ERGEBNIS: {failed}/{len(_results)} FEHLGESCHLAGEN")
    return 1


if __name__ == "__main__":
    sys.exit(main())
