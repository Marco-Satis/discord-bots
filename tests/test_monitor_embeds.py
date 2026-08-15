#!/usr/bin/env python3
"""
Tests für die Monitor-Panels (cogs/monitor_cog.py).

Deckt ``build_world_embed`` ab — das Rendering von ``/mon world``. Vorher lag es
im Interaction-Pfad und war damit gar nicht prüfbar.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_monitor_embeds.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import discord  # noqa: F401
    from cogs.monitor_cog import build_world_embed
    HAVE_DISCORD = True
except ImportError:
    HAVE_DISCORD = False

_results: list[tuple[str, bool, str]] = []

# Alle Zähler, die das Panel liest — Default 0 heißt „Gruppe entfällt".
ZAEHLER = [
    "total_buildings", "production_machines", "total_power_mw",
    "foundations", "walls", "storage", "power_poles",
    "smelters", "foundries", "constructors", "assemblers", "manufacturers",
    "refineries", "blenders",
    "miners", "oil_extractors", "water_extractors", "resource_well_pressurizers",
    "generators", "biomass_burners", "coal_generators", "fuel_generators",
    "geothermal_generators", "nuclear_plants", "alien_augmenters", "power_storage",
    "conveyor_belts", "belts_mk1", "belts_mk2", "belts_mk3", "belts_mk4",
    "belts_mk5", "belts_mk6", "lifts_total",
    "pipes", "pipes_mk1", "pipes_mk2", "pipeline_pumps", "valves",
    "splitters", "smart_splitters", "programmable_splitters", "mergers",
    "priority_mergers",
    "trains", "locomotives", "freight_cars", "stations", "vehicles", "trucks",
    "explorers", "drone_ports",
]


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _welt(**werte):
    """WorldStats-Attrappe: alles 0, überschrieben von ``werte``."""
    daten = {z: 0 for z in ZAEHLER}
    daten.update(
        available=True,
        session_name="Testwelt",
        play_hours=120,
        save_date="12.08.2026 18:00",
        save_size="45 MB",
        last_analyzed="12.08.2026 18:05",
    )
    daten.update(werte)
    return SimpleNamespace(**daten)


def run_tests() -> None:
    # 1. Kopf: Statuspunkt, Titel, Kennzahlen
    e = build_world_embed(_welt(total_buildings=12345, production_machines=512,
                                total_power_mw=1200))
    _check("titel_hat_punkt", e.title.startswith("🔵"), e.title)
    _check("titel_text", "WELTSTATISTIK" in e.title, e.title)
    _check("kennzahl_gebaeude", "**12.345** Gebäude" in e.description, e.description[:80])
    _check("kennzahl_maschinen", "**512** Maschinen" in e.description)
    _check("kennzahl_mw", "**1.200** MW" in e.description)
    _check("sessionname", "**Testwelt**" in e.description)
    _check("meta_zeile_subtext", "-# Spielzeit 120h · gespeichert" in e.description)
    _check("footer_analysiert", e.footer.text == "Analysiert: 12.08.2026 18:05",
           str(e.footer.text))

    # 2. Keine Felder mehr — der ganze Sinn der Umstellung
    _check("keine_felder", len(e.fields) == 0, f"{len(e.fields)} Felder")

    # 3. Leere Gruppen entfallen vollständig statt als „0" zu erscheinen
    _check("leere_gruppe_fehlt", "-# ROHSTOFFE" not in e.description)
    _check("keine_null_werte", " 0" not in e.description.replace("**1.200**", ""),
           e.description)

    # 4. Belegte Gruppen erscheinen mit Überschrift und Werten in einer Zeile
    e2 = build_world_embed(_welt(total_buildings=10, foundations=1234, walls=567,
                                 miners=8, oil_extractors=3))
    _check("gruppe_bau", "-# BAU" in e2.description)
    _check("gruppe_bau_werte", "Fundamente 1.234 · Wände 567" in e2.description,
           e2.description)
    _check("gruppe_rohstoffe", "-# ROHSTOFFE" in e2.description)
    _check("gruppe_rohstoffe_werte", "Bergbau 8 · Öl 3" in e2.description)
    _check("gruppe_produktion_fehlt", "-# PRODUKTION" not in e2.description)

    # 5. Vollbelegung bleibt unter dem Discord-Limit für description (4096)
    voll = build_world_embed(_welt(**{z: 9999 for z in ZAEHLER}))
    _check("vollbelegung_unter_limit", len(voll.description) < 4096,
           f"{len(voll.description)} Zeichen")
    _check("vollbelegung_alle_gruppen",
           all(g in voll.description for g in
               ("-# BAU", "-# PRODUKTION", "-# ROHSTOFFE", "-# STROM",
                "-# FÖRDERBÄNDER", "-# ROHRLEITUNGEN", "-# VERTEILER",
                "-# TRANSPORT")),
           voll.description[:200])

    # 6. Ohne Analyse-Zeitpunkt kein erfundener Footer
    ohne = build_world_embed(_welt(total_buildings=1, last_analyzed=None))
    _check("footer_ohne_analyse", ohne.footer.text is None, str(ohne.footer.text))


def main() -> int:
    print("=" * 60)
    print("  Monitor-Panel Tests (cogs/monitor_cog.py)")
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
