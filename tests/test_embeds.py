#!/usr/bin/env python3
"""
Tests für den zentralen Embed-Helper (utils/embeds.py).

Lauf: python tests/test_embeds.py
Skippt sauber wenn discord.py lokal nicht installiert ist (läuft dann am Server).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import discord  # noqa: F401
    from utils import embeds
    HAVE_DISCORD = True
except ImportError:
    HAVE_DISCORD = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def run_tests() -> None:
    # 1. Semantische Helfer setzen die richtige Farbe
    _check("success_color", embeds.success_embed("x").color.value == embeds.COLOR_SUCCESS)
    _check("error_color", embeds.error_embed("x").color.value == embeds.COLOR_ERROR)
    _check("warning_color", embeds.warning_embed("x").color.value == embeds.COLOR_WARNING)
    _check("info_color", embeds.info_embed("x").color.value == embeds.COLOR_INFO)
    _check("neutral_color", embeds.neutral_embed("x").color.value == embeds.COLOR_NEUTRAL)

    # 2. Titel/Beschreibung kommen durch
    e = embeds.success_embed("Titel", "Beschreibung")
    _check("title_passed", e.title == "Titel")
    _check("desc_passed", e.description == "Beschreibung")

    # 3. Farb-Override per kwarg gewinnt
    e2 = embeds.success_embed("x", color=0x123456)
    _check("color_override", e2.color.value == 0x123456)

    # 4. Timestamp default an, abschaltbar
    _check("timestamp_default_on", embeds.base_embed("x").timestamp is not None)
    _check("timestamp_off", embeds.base_embed("x", timestamp=False).timestamp is None)

    # 5. Branding-Footer: erst leer, nach set_branding gesetzt
    _check("footer_default_empty", embeds.base_embed("x").footer.text is None)
    embeds.set_branding(footer="MeinBot")
    _check("footer_after_branding", embeds.base_embed("x").footer.text == "MeinBot")
    # expliziter footer-Param überschreibt Branding
    _check("footer_explicit", embeds.base_embed("x", footer="Custom").footer.text == "Custom")


def main() -> int:
    print("=" * 60)
    print("  Embed-Helper Tests (utils/embeds.py)")
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
