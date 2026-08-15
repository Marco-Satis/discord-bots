#!/usr/bin/env python3
"""
Tests für den JSON→SQLite-Import (modules/database/json_importer.py).

Anlass ist ein Befund, der nur im Ernstfall auffällt: `_import_leveling`
schrieb ohne `guild_id`, seit Schema v6 eine NOT-NULL-Spalte. `INSERT OR IGNORE`
schluckte die Constraint-Verletzung, der Zähler lief trotzdem hoch — der Import
meldete Erfolg bei null geschriebenen Zeilen. Beim Wiederaufbau aus den
JSON-Dateien wäre die komplette XP-Historie still verschwunden.

Der Test legt deshalb eine echte SQLite-Datenbank mit dem echten Schema an und
prüft, was tatsächlich in der Tabelle landet.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_json_importer.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import aiosqlite
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []

# Das Schema, wie die Migration es hinterlässt (guild_id NOT NULL, UNIQUE-Paar).
SCHEMA_LEVELING = """
CREATE TABLE leveling (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 0,
    messages INTEGER DEFAULT 0,
    voice_minutes INTEGER DEFAULT 0,
    last_xp_time TIMESTAMP,
    UNIQUE(guild_id, user_id)
)
"""


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def _lauf() -> None:
    from modules.database import json_importer

    with tempfile.TemporaryDirectory() as ordner:
        # leveling.json bereitstellen, dort wo der Importer sie sucht
        daten_ordner = Path(ordner) / "data"
        daten_ordner.mkdir()
        (daten_ordner / "leveling.json").write_text(json.dumps({
            "111": {"xp": 500, "level": 3, "total_messages": 42, "voice_minutes": 10},
            "222": {"xp": 900, "level": 5, "total_messages": 90, "voice_minutes": 30},
        }), encoding="utf-8")

        # Der Leveling-Import liest aus ADMIN_DATA_DIR.
        json_importer.ADMIN_DATA_DIR = daten_ordner
        json_importer.DATA_DIR = daten_ordner
        os.environ["GUILD_ID"] = "777"

        async with aiosqlite.connect(Path(ordner) / "test.db") as db:
            await db.execute(SCHEMA_LEVELING)
            await db.commit()

            geschrieben = await json_importer._import_leveling(db)
            await db.commit()

            cursor = await db.execute("SELECT COUNT(*) FROM leveling")
            (anzahl,) = await cursor.fetchone()

            _check("import_meldet_zwei", geschrieben == 2, f"gemeldet: {geschrieben}")
            _check("zwei_zeilen_wirklich_da", anzahl == 2, f"in der Tabelle: {anzahl}")
            _check("meldung_deckt_sich_mit_tabelle", geschrieben == anzahl,
                   f"gemeldet {geschrieben}, tatsaechlich {anzahl}")

            cursor = await db.execute(
                "SELECT guild_id, xp FROM leveling WHERE user_id = '111'"
            )
            zeile = await cursor.fetchone()
            _check("guild_id_gesetzt", zeile is not None and zeile[0] == "777",
                   str(zeile))
            _check("werte_uebernommen", zeile is not None and zeile[1] == 500, str(zeile))

    # --- Ein fehlgeschlagener Schritt reisst die anderen nicht mit ---
    with tempfile.TemporaryDirectory() as ordner:
        daten_ordner = Path(ordner) / "data"
        daten_ordner.mkdir()
        json_importer.DATA_DIR = daten_ordner
        json_importer.ADMIN_DATA_DIR = daten_ordner
        json_importer.MONITOR_DATA_DIR = daten_ordner

        async with aiosqlite.connect(Path(ordner) / "test2.db") as db:
            await db.execute(SCHEMA_LEVELING)
            await db.commit()

            echte_funktion = json_importer._import_events

            async def _kaputt(db):
                raise RuntimeError("Testfehler")

            json_importer._import_events = _kaputt
            try:
                stats = await json_importer.import_all(db)
            finally:
                json_importer._import_events = echte_funktion

            _check("fehlschlag_wird_gemeldet",
                   "_fehlgeschlagen" in stats and "events" in stats["_fehlgeschlagen"],
                   str(stats.get("_fehlgeschlagen")))
            _check("andere_schritte_liefen_trotzdem",
                   "reaction_roles" in stats,
                   "der Import brach nach dem Fehler ab")


def run_tests() -> None:
    asyncio.run(_lauf())


def main() -> int:
    print("=" * 60)
    print("  JSON-Import (modules/database/json_importer.py)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] aiosqlite lokal nicht installiert — Test läuft am Server.")
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
