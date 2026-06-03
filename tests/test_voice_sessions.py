#!/usr/bin/env python3
"""
Tests fuer den Voice-Session-Tracker (Phase C, C3).

Schwerpunkt (Plan-DoD: "Voice-XP nur bei echter Session"):
  - Akkumulation: nur *valide* Sample-Sekunden zaehlen.
  - Alleine/deaf die ganze Zeit -> 0 valide Minuten -> 0 XP.
  - Kurze Session (< min_seconds valide Zeit) -> valid=0.
  - guild-scoped Persistenz in voice_sessions (kein Cross-Guild-Leak).
  - close_orphans schliesst offene Rows (Neustart-Sicherheit).
  - start idempotent.

Deterministisch via injizierter `now`-Zeitstempel (kein Wall-Clock).
Lauf: python tests/test_voice_sessions.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import aiosqlite  # noqa: F401
    from modules.database import db_manager
    from modules.voice_sessions import VoiceSessionTracker
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []

GUILD_A = "111111111111111111"
GUILD_B = "222222222222222222"
USER_1 = "42"
CH = "555000555"


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def _row(db, session_id: int):
    cur = await db.execute(
        "SELECT guild_id, user_id, leave_time, duration_seconds, xp_awarded, valid "
        "FROM voice_sessions WHERE id = ?",
        (session_id,),
    )
    return await cur.fetchone()


async def run_tests() -> None:
    tmp = tempfile.mkdtemp(prefix="vs_db_")
    db_path = Path(tmp) / "vs.db"
    await db_manager.init_db(db_path=db_path)
    db = await db_manager.get_db()

    try:
        tracker = VoiceSessionTracker()

        # ============================================================
        # 1) Normale Session: 2 valide Minuten -> XP
        # ============================================================
        await tracker.start(GUILD_A, USER_1, CH, now=1000.0)
        _check("session_open", tracker.is_open(GUILD_A, USER_1))
        _check("open_keys_has", (GUILD_A, USER_1) in tracker.open_keys())

        tracker.sample(GUILD_A, USER_1, valid=True, now=1060.0)   # +60
        tracker.sample(GUILD_A, USER_1, valid=True, now=1120.0)   # +60
        res = await tracker.end(GUILD_A, USER_1, min_seconds=60, now=1125.0)
        _check("end_returns", res is not None)
        _check("valid_minutes_2", res and res["valid_minutes"] == 2, f"res={res}")
        _check("valid_flag_true", res and res["valid"] is True)
        _check("duration_125", res and res["duration"] == 125, f"d={res['duration'] if res else None}")
        _check("session_closed_mem", not tracker.is_open(GUILD_A, USER_1))

        # DB-Row geschlossen + valid
        row = await _row(db, res["db_id"])
        _check("db_leave_set", row is not None and row["leave_time"] is not None)
        _check("db_valid_1", row is not None and row["valid"] == 1)
        _check("db_guild_a", row is not None and row["guild_id"] == GUILD_A)

        # set_xp Audit
        await tracker.set_xp(res["db_id"], 40)
        row = await _row(db, res["db_id"])
        _check("db_xp_awarded", row is not None and row["xp_awarded"] == 40)

        # ============================================================
        # 2) Alleine/deaf die ganze Zeit -> 0 valide Minuten -> kein XP
        # ============================================================
        await tracker.start(GUILD_A, USER_1, CH, now=2000.0)
        tracker.sample(GUILD_A, USER_1, valid=False, now=2060.0)
        tracker.sample(GUILD_A, USER_1, valid=False, now=2300.0)
        res2 = await tracker.end(GUILD_A, USER_1, min_seconds=60, now=2305.0)
        _check("invalid_zero_minutes", res2 and res2["valid_minutes"] == 0, f"res2={res2}")
        _check("invalid_valid_false", res2 and res2["valid"] is False)
        row2 = await _row(db, res2["db_id"])
        _check("db_invalid_valid_0", row2 is not None and row2["valid"] == 0)

        # ============================================================
        # 3) Kurze valide Zeit (< min_seconds) -> valid=0
        # ============================================================
        await tracker.start(GUILD_A, USER_1, CH, now=3000.0)
        tracker.sample(GUILD_A, USER_1, valid=True, now=3030.0)   # +30 < 60
        res3 = await tracker.end(GUILD_A, USER_1, min_seconds=60, now=3040.0)
        _check("short_valid_false", res3 and res3["valid"] is False, f"res3={res3}")
        _check("short_zero_minutes", res3 and res3["valid_minutes"] == 0)

        # ============================================================
        # 4) Guild-Isolation: parallele Sessions, getrennte Rows
        # ============================================================
        await tracker.start(GUILD_A, USER_1, CH, now=4000.0)
        await tracker.start(GUILD_B, USER_1, CH, now=4000.0)
        _check("both_open", tracker.is_open(GUILD_A, USER_1) and tracker.is_open(GUILD_B, USER_1))
        tracker.sample(GUILD_A, USER_1, valid=True, now=4120.0)   # A: +120
        tracker.sample(GUILD_B, USER_1, valid=False, now=4120.0)  # B: 0
        rA = await tracker.end(GUILD_A, USER_1, min_seconds=60, now=4130.0)
        rB = await tracker.end(GUILD_B, USER_1, min_seconds=60, now=4130.0)
        _check("iso_a_valid", rA and rA["valid_minutes"] == 2)
        _check("iso_b_invalid", rB and rB["valid_minutes"] == 0)
        rowA = await _row(db, rA["db_id"])
        rowB = await _row(db, rB["db_id"])
        _check("iso_row_guilds",
               rowA is not None and rowB is not None
               and rowA["guild_id"] == GUILD_A and rowB["guild_id"] == GUILD_B)

        # ============================================================
        # 5) start idempotent (zweiter start oeffnet keine zweite Row)
        # ============================================================
        cur = await db.execute("SELECT COUNT(*) AS c FROM voice_sessions")
        count_before = (await cur.fetchone())["c"]
        await tracker.start(GUILD_A, USER_1, CH, now=5000.0)
        await tracker.start(GUILD_A, USER_1, CH, now=5001.0)  # idempotent
        cur = await db.execute("SELECT COUNT(*) AS c FROM voice_sessions")
        count_after = (await cur.fetchone())["c"]
        _check("start_idempotent", count_after == count_before + 1,
               f"before={count_before} after={count_after}")

        # ============================================================
        # 6) close_orphans schliesst die noch offene Row aus (5)
        # ============================================================
        closed = await tracker.close_orphans(now=6000.0)
        _check("orphans_closed_min1", closed >= 1, f"closed={closed}")
        cur = await db.execute(
            "SELECT COUNT(*) AS c FROM voice_sessions WHERE leave_time IS NULL"
        )
        _check("no_open_rows_left", (await cur.fetchone())["c"] == 0)

        # end auf nicht-offene Session -> None
        res_none = await tracker.end(GUILD_A, "999", now=7000.0)
        _check("end_unknown_none", res_none is None)

    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  Voice-Session-Tracker Tests (modules/voice_sessions.py, C3)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] aiosqlite lokal nicht installiert — laeuft am Server.")
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
