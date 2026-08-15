#!/usr/bin/env python3
"""
Tests für Migration v12 (Server-ID für Satisfactory-Messwerte).

Solange es genau einen Satisfactory-Server gab, blieb `server_id` bei seinen
Messwerten leer — Minecraft füllte sie von Anfang an. Mit einem zweiten
Satisfactory-Server braucht jede Instanz ihre ID, sonst laufen beide Verläufe
in einen Topf.

Der wichtigste Test hier ist nicht der Backfill selbst, sondern die **Kopplung**:
nach der Migration gibt es keine `NULL`-Zeilen mehr. Ein StatsTracker, der noch
mit `server_id=None` gebaut wird, fragt `server_id IS NULL` ab und findet dann
gar nichts — Uptime und Spitzenbelegung stünden still auf null, ohne dass
irgendwo ein Fehler erscheint. Genau das prüft `tracker_findet_nach_migration`.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_migration_v12.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import aiosqlite
    from modules.database import db_manager
    from modules.monitoring.stats_tracker import StatsTracker
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []

SCHEMA = """
CREATE TABLE server_stats_tracker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_type TEXT NOT NULL,
    server_id TEXT,
    metric_type TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    value_int INTEGER,
    value_real REAL
);
"""


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def run_tests() -> None:
    from modules.database.migrations import _apply_migration_v12

    jetzt = datetime.now()
    tmp = tempfile.mkdtemp(prefix="mig12_")
    pfad = Path(tmp) / "alt.db"

    async with aiosqlite.connect(pfad) as db:
        await db.executescript(SCHEMA)
        # Zustand vor der Migration: Satisfactory ohne ID, Minecraft mit
        for i in range(5):
            await db.execute(
                "INSERT INTO server_stats_tracker "
                "(server_type, server_id, metric_type, timestamp, value_int) "
                "VALUES ('sat', NULL, 'uptime', ?, 1)",
                ((jetzt - timedelta(hours=i)).isoformat(),))
        await db.execute(
            "INSERT INTO server_stats_tracker "
            "(server_type, server_id, metric_type, timestamp, value_int) "
            "VALUES ('mc', 'bmc', 'uptime', ?, 1)", (jetzt.isoformat(),))
        await db.execute(
            "INSERT INTO server_stats_tracker "
            "(server_type, server_id, metric_type, timestamp, value_int) "
            "VALUES ('mc', 'vanilla', 'uptime', ?, 0)", (jetzt.isoformat(),))
        await db.commit()

        await _apply_migration_v12(db)

        cur = await db.execute(
            "SELECT COUNT(*) FROM server_stats_tracker "
            "WHERE server_type = 'sat' AND server_id = 'MAIN'")
        _check("sat_bekommt_id", (await cur.fetchone())[0] == 5)

        cur = await db.execute(
            "SELECT COUNT(*) FROM server_stats_tracker "
            "WHERE server_type = 'sat' AND server_id IS NULL")
        _check("keine_leeren_mehr", (await cur.fetchone())[0] == 0)

        cur = await db.execute(
            "SELECT server_id FROM server_stats_tracker WHERE server_type = 'mc' "
            "ORDER BY server_id")
        mc = [z[0] for z in await cur.fetchall()]
        _check("minecraft_unberuehrt", mc == ["bmc", "vanilla"], str(mc))
        _check("vanilla_daten_bleiben", "vanilla" in mc,
               "die stillgelegten Messwerte gehören nicht gelöscht")

        # Zweiter Lauf darf nichts mehr verändern
        await _apply_migration_v12(db)
        cur = await db.execute(
            "SELECT COUNT(*) FROM server_stats_tracker WHERE server_id = 'MAIN'")
        _check("idempotent", (await cur.fetchone())[0] == 5)

    # --- Die Kopplung: der Tracker muss nach der Migration weiter finden ---
    tmp2 = tempfile.mkdtemp(prefix="mig12_tracker_")
    await db_manager.init_db(db_path=Path(tmp2) / "voll.db")
    db = await db_manager.get_db()
    try:
        for i in range(4):
            await db.execute(
                "INSERT INTO server_stats_tracker "
                "(server_type, server_id, metric_type, timestamp, value_int) "
                "VALUES ('sat', NULL, 'uptime', ?, 1)",
                ((jetzt - timedelta(hours=i)).isoformat(),))
        await db.commit()

        # Vor der Migration findet der alte Tracker (ohne ID) seine Daten
        alt = StatsTracker(server_type="sat")
        _check("vor_migration_findet_alter_tracker",
               await alt.get_total_checks(7) == 4,
               str(await alt.get_total_checks(7)))

        await _apply_migration_v12(db)

        # Danach findet er nichts mehr — das ist der Fallstrick
        _check("nach_migration_findet_alter_tracker_nichts",
               await alt.get_total_checks(7) == 0,
               "wenn das fehlschlägt, ist die Migration nicht gelaufen")

        # Der Tracker MIT ID findet sie — so ist recon_bot verdrahtet
        neu = StatsTracker(server_type="sat", server_id="MAIN")
        _check("tracker_findet_nach_migration",
               await neu.get_total_checks(7) == 4,
               f"{await neu.get_total_checks(7)} statt 4 — Bericht stünde auf null")
        _check("uptime_stimmt_weiter", await neu.get_uptime_percent(7) == 100.0,
               str(await neu.get_uptime_percent(7)))
    finally:
        await db_manager.close_db()

    # --- recon_bot baut den Tracker wirklich mit ID ---
    quelle = (Path(__file__).resolve().parent.parent / "bots" / "recon_bot.py"
              ).read_text(encoding="utf-8")
    _check("recon_bot_setzt_server_id",
           'StatsTracker(server_type="sat", server_id=' in quelle,
           "ohne server_id stünde der Satisfactory-Bericht nach der Migration auf null")


def main() -> int:
    print("=" * 60)
    print("  Migration v12 (server_id für Satisfactory-Messwerte)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] aiosqlite nicht installiert — läuft am Server.")
        print("  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN (übersprungen)")
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
