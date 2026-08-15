#!/usr/bin/env python3
"""
Tests für den zentralen Embed-Helper (utils/embeds.py).

Lauf: python tests/test_embeds.py
Skippt sauber wenn discord.py lokal nicht installiert ist (läuft dann am Server).
"""
import asyncio
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

    # 4b. Ein übergebener Zeitpunkt wird übernommen, nicht als "ja, irgendeiner"
    #     gelesen. Regression zu Review-Befund Q-03: der Codemod ließ
    #     `timestamp=datetime.now(timezone.utc)` an ~9 Stellen stehen; ein
    #     datetime ist immer wahr, der Wert wäre still verworfen worden.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    fest = _dt(2026, 8, 14, 3, 0, tzinfo=_tz.utc)
    _check("timestamp_datetime_uebernommen",
           embeds.base_embed("x", timestamp=fest).timestamp == fest,
           str(embeds.base_embed("x", timestamp=fest).timestamp))
    # discord.py hängt naiven Zeitstempeln UTC an — deshalb hier gegen
    # aware-now vergleichen, sonst wirft der Vergleich selbst.
    _check("timestamp_true_ist_jetzt",
           abs(embeds.base_embed("x").timestamp - _dt.now(_tz.utc)) < _td(seconds=5))
    _check("timestamp_datetime_durch_helfer",
           embeds.success_embed("x", timestamp=fest).timestamp == fest)
    _check("timestamp_datetime_durch_hud",
           embeds.hud_embed("X", timestamp=fest).timestamp == fest)

    # 5. Branding-Footer: erst leer, nach set_branding gesetzt
    _check("footer_default_empty", embeds.base_embed("x").footer.text is None)
    # Ohne Branding-Icon darf gar kein icon_url im Payload stehen. discord.utils.MISSING
    # wird von set_footer NICHT verworfen — es landete als "..." im Feld, und Discord
    # antwortete mit 400 "Not a well formed URL". Das hat live das Status-Panel gekippt.
    _check("footer_ohne_icon_kein_feld",
           "icon_url" not in (embeds.base_embed("x", footer="F").to_dict().get("footer") or {}),
           str(embeds.base_embed("x", footer="F").to_dict().get("footer")))
    _check("footer_payload_nur_text",
           embeds.base_embed("x", footer="F").to_dict()["footer"] == {"text": "F"},
           str(embeds.base_embed("x", footer="F").to_dict().get("footer")))
    embeds.set_branding(footer="MeinBot")
    _check("footer_after_branding", embeds.base_embed("x").footer.text == "MeinBot")
    # expliziter footer-Param überschreibt Branding
    _check("footer_explicit", embeds.base_embed("x", footer="Custom").footer.text == "Custom")

    # 6. HUD-Palette: jede semantische Farbe ist eindeutig
    palette = [embeds.COLOR_BRAND, embeds.COLOR_SUCCESS, embeds.COLOR_ERROR,
               embeds.COLOR_WARNING, embeds.COLOR_INFO, embeds.COLOR_NEUTRAL,
               embeds.COLOR_PROGRESS]
    _check("palette_eindeutig", len(set(palette)) == len(palette))
    _check("brand_ist_gold", embeds.COLOR_BRAND == 0xF2C14E)
    _check("default_ist_brand", embeds.base_embed("x").color.value == embeds.COLOR_BRAND)

    # 7. Zustand steuert Farbe UND Punkt (Farbe allein reicht nicht)
    _check("state_color_ok", embeds.state_color("ok") == embeds.COLOR_SUCCESS)
    _check("state_color_error", embeds.state_color("error") == embeds.COLOR_ERROR)
    _check("state_color_unbekannt_faellt_auf_info", embeds.state_color("quatsch") == embeds.COLOR_INFO)
    _check("state_title_hat_punkt", embeds.state_title("ok", "Titel").startswith("🟢"))
    _check("state_title_error_punkt", embeds.state_title("error", "Titel").startswith("🔴"))
    _check("state_color_progress", embeds.state_color("progress") == embeds.COLOR_PROGRESS)

    # 7b. Zahlen deutsch — ein Helfer, nicht pro Cog eine eigene Formel
    from utils.ui_kit import zahl
    _check("zahl_tausenderpunkt", zahl(12345) == "12.345", zahl(12345))
    _check("zahl_klein_unveraendert", zahl(42) == "42", zahl(42))
    _check("zahl_float_gerundet", zahl(1234.6) == "1.235", zahl(1234.6))

    # 8. hud_embed baut Kopf aus Kennzahlen + Balken
    h = embeds.hud_embed("SERVERSTATUS", state="ok",
                         meta=[("3", "Server"), ("4", "Spieler")], bar=(3, 8))
    _check("hud_farbe", h.color.value == embeds.COLOR_SUCCESS)
    _check("hud_punkt_im_titel", h.title.startswith("🟢"))
    _check("hud_kennzahlen", "**3** Server" in h.description)
    _check("hud_balken", "▬" in h.description and "▭" in h.description)
    _check("hud_ohne_alles", embeds.hud_embed("NUR TITEL").description is None)

    # 9. status_field: Punkt, Auslastung, Balken, Notiz
    sf = embeds.status_field("Satisfactory", "ok", value=3, maximum=8, note="Uptime 4d")
    _check("statusfield_punkt", sf.startswith("🟢"))
    _check("statusfield_auslastung", "`3/8`" in sf)
    _check("statusfield_balken", "▰" in sf and "▱" in sf)
    _check("statusfield_notiz", "-# Uptime 4d" in sf)
    _check("statusfield_ohne_zahlen", "`" not in embeds.status_field("X", "off", note="gestoppt"))

    # 10. Panel-Baukasten: Budget-Rechnung und Aufbau
    # LayoutView braucht einen laufenden Event-Loop (Timeout-Future).
    from utils import panels

    async def _panel_checks() -> None:
        v = panels.hud_panel("TEST", meta=[("1", "x")], bar=(1, 2),
                             rows=[panels.panel_row("Zeile")], footer_note="jetzt")
        _check("panel_eine_top_komponente", len(v.to_components()) == 1)
        _check("panel_unter_limit", v._total_children <= panels.COMPONENT_LIMIT)

    asyncio.run(_panel_checks())
    _check("budget_rechnet", panels.row_budget(10) == 10)
    _check("budget_nie_negativ", panels.row_budget(100) == 0)

    # 11. Es gibt genau EINE Balken-Optik im Bot (vorher drei nebeneinander:
    #     utils/formatting "[###---]", utils/ui_kit "▰▱", monitor_cog "█░")
    from utils import formatting
    from utils.ui_kit import BAR_STYLES, progress_bar

    voll, leer = BAR_STYLES["box"]
    alt = formatting.progress_bar(30, 100)
    _check("formatting nutzt Hausstil", voll in alt and leer in alt, alt)
    _check("formatting ohne alte Klammern", "[" not in alt and "#" not in alt, alt)
    _check("formatting behaelt Prozent", alt.endswith("30%"), alt)
    _check("formatting gleicher Balken wie ui_kit", alt.startswith(progress_bar(30, 100, 10)), alt)
    _check("formatting maximum 0 bricht nicht", formatting.progress_bar(5, 0) == leer * 10,
           formatting.progress_bar(5, 0))

    from cogs.monitor_cog import MonitorCog
    _check("monitor_cog nutzt denselben Balken",
           MonitorCog._make_bar(30) == progress_bar(30, 100, 10),
           MonitorCog._make_bar(30))


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
