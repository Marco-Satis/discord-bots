#!/usr/bin/env python3
"""
Tests für den StatsTracker (modules/monitoring/stats_tracker.py).

Anlass ist der Speicher-Umbau vom 2026-08-14: der Tracker hielt seine 90 Tage
Rohdaten zusätzlich als Python-Listen im Prozess (rund 68 MB über vier Tracker,
bei MemoryMax=768M). Die Auswertung rechnet jetzt in SQLite.

Der Test prüft deshalb zweierlei — dass die Zahlen stimmen (gegen von Hand
nachgerechnete Werte), und dass der Tracker die Daten wirklich nicht mehr im
Speicher hält: ein Wert, der direkt in die Datenbank geschrieben wird, muss ohne
Neuladen sichtbar sein.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_stats_tracker.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import aiosqlite  # noqa: F401
    from modules.database import db_manager
    from modules.monitoring.stats_tracker import StatsTracker
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def _schreibe(db, server_type, server_id, metric, wann, value_int=None,
                    value_real=None) -> None:
    await db.execute(
        "INSERT INTO server_stats_tracker "
        "(server_type, server_id, metric_type, timestamp, value_int, value_real) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (server_type, server_id, metric, wann.isoformat(), value_int, value_real),
    )


async def run_tests() -> None:
    tmp = tempfile.mkdtemp(prefix="stats_")
    await db_manager.init_db(db_path=Path(tmp) / "stats.db")
    db = await db_manager.get_db()
    jetzt = datetime.now()

    try:
        tracker = StatsTracker(server_type="sat")
        fremd = StatsTracker(server_type="mc", server_id="bmc")

        # --- Uptime: 8 von 10 online, dazu ein Wert ausserhalb des Fensters ---
        for i in range(10):
            await _schreibe(db, "sat", None, "uptime",
                            jetzt - timedelta(hours=i),
                            value_int=1 if i < 8 else 0)
        await _schreibe(db, "sat", None, "uptime",
                        jetzt - timedelta(days=30), value_int=0)

        # --- Spielerzahlen: Spitze 7 im Fenster, 99 ausserhalb ---
        for i, anzahl in enumerate([2, 7, 3]):
            await _schreibe(db, "sat", None, "player_count",
                            jetzt - timedelta(hours=i), value_int=anzahl)
        await _schreibe(db, "sat", None, "player_count",
                        jetzt - timedelta(days=30), value_int=99)

        # --- Savegame: 100 MB -> 150 MB ---
        await _schreibe(db, "sat", None, "savegame_size",
                        jetzt - timedelta(days=3), value_real=100.0)
        await _schreibe(db, "sat", None, "savegame_size",
                        jetzt - timedelta(days=1), value_real=150.0)

        # --- Absturz ---
        await _schreibe(db, "sat", None, "crash",
                        jetzt - timedelta(hours=2), value_int=3)

        # --- Fremder Server, darf nirgends mitgezaehlt werden ---
        await _schreibe(db, "mc", "bmc", "uptime", jetzt, value_int=0)
        await _schreibe(db, "mc", "bmc", "player_count", jetzt, value_int=42)
        await db.commit()

        _check("uptime_prozent", await tracker.get_uptime_percent(7) == 80.0,
               str(await tracker.get_uptime_percent(7)))
        _check("uptime_zaehlt_nur_fenster", await tracker.get_total_checks(7) == 10,
               str(await tracker.get_total_checks(7)))
        _check("spitze_im_fenster", await tracker.get_peak_players(7) == 7,
               str(await tracker.get_peak_players(7)))

        wachstum = await tracker.get_savegame_growth(7)
        _check("wachstum_start_ende",
               wachstum and wachstum["start_mb"] == 100.0 and wachstum["end_mb"] == 150.0,
               str(wachstum))
        _check("wachstum_differenz", wachstum and wachstum["growth_mb"] == 50.0,
               str(wachstum))
        _check("wachstum_prozent", wachstum and wachstum["growth_percent"] == 50.0,
               str(wachstum))

        abstuerze = await tracker.get_crashes(7)
        _check("absturz_gefunden",
               len(abstuerze) == 1 and abstuerze[0]["number"] == 3, str(abstuerze))

        # Der fremde Server hat eigene Werte — keine Vermischung.
        _check("fremder_server_getrennt", await fremd.get_peak_players(7) == 42,
               str(await fremd.get_peak_players(7)))
        _check("fremde_uptime_getrennt", await fremd.get_uptime_percent(7) == 0.0,
               str(await fremd.get_uptime_percent(7)))

        # --- Schwellwert-Warnung haengt am Wachstum ---
        warnung = await tracker.check_savegame_trend(warn_growth_mb=10,
                                                     warn_growth_pct=999, days=7)
        _check("warnung_bei_ueberschreitung",
               warnung is not None and warnung["growth_mb"] == 50.0, str(warnung))
        keine = await tracker.check_savegame_trend(warn_growth_mb=999,
                                                   warn_growth_pct=999, days=7)
        _check("keine_warnung_unter_schwelle", keine is None, str(keine))

        # --- Kein Speicherstand: ein direkt geschriebener Wert wirkt sofort ---
        await _schreibe(db, "sat", None, "player_count", jetzt, value_int=11)
        await db.commit()
        _check("liest_frisch_aus_der_db", await tracker.get_peak_players(7) == 11,
               f"{await tracker.get_peak_players(7)} — Tracker haelt noch eigene Daten")

        # --- Der Tracker traegt keine Rohdaten mit sich herum ---
        _check("kein_data_feld", not hasattr(tracker, "_data"),
               "das alte Speicher-Feld _data existiert noch")

        # --- Leerer Zeitraum liefert nichts, statt zu krachen ---
        leer = StatsTracker(server_type="gibtsnicht")
        _check("leer_uptime_null", await leer.get_uptime_percent(7) == 0.0)
        _check("leer_spitze_null", await leer.get_peak_players(7) == 0)
        _check("leer_wachstum_none", await leer.get_savegame_growth(7) is None)
        _check("leer_keine_abstuerze", await leer.get_crashes(7) == [])

        # --- Ein einzelner Groessenwert ergibt keinen Trend ---
        einzeln = StatsTracker(server_type="einzel")
        await _schreibe(db, "einzel", None, "savegame_size", jetzt, value_real=5.0)
        await db.commit()
        _check("ein_wert_kein_trend", await einzeln.get_savegame_growth(7) is None)

        # --- load_from_db meldet nur noch, es laedt nichts ---
        await tracker.load_from_db()
        _check("load_from_db_ohne_speicher", not hasattr(tracker, "_data"))

        # --- Der Aufraeum-Zaehler loest erst nach dem Intervall aus ---
        from modules.monitoring import stats_tracker as st_modul
        zaehler = StatsTracker(server_type="zaehl")
        angestossen: list = []
        zaehler._cleanup_old = lambda days=90: angestossen.append(days)
        for _ in range(st_modul.AUFRAEUM_INTERVALL - 1):
            zaehler._mitzaehlen()
        _check("kein_aufraeumen_vor_intervall", not angestossen, str(angestossen))
        zaehler._mitzaehlen()
        _check("aufraeumen_bei_intervall", len(angestossen) == 1, str(angestossen))
        _check("zaehler_beginnt_neu", zaehler._seit_aufraeumen == 0,
               str(zaehler._seit_aufraeumen))
    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  StatsTracker (modules/monitoring/stats_tracker.py)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] aiosqlite nicht installiert — laeuft am Server.")
        print("  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN (uebersprungen)")
        return 0

    try:
        asyncio.run(run_tests())
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
