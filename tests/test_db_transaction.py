#!/usr/bin/env python3
"""
Tests fuer den C3-Transaktions-Context-Manager (modules/database/db_manager.transaction).

Prueft die Garantien aus HANDOFF_C3_get_db-write-lock.md:
  - COMMIT bei Erfolg, ROLLBACK bei Exception (atomar).
  - Zwei gleichzeitige transaction()-Bloecke serialisieren (kein Lost-Write,
    kein Phantom-Commit / Interleave) — die dedizierte Connection + _txn_lock.
  - set_role_grants: zwei Rollen gleichzeitig bleiben isoliert; ein Partial-Fail
    (Abbruch nach DELETE) rollt zurueck und laesst die alten Grants stehen (M39).

Lauf: python tests/test_db_transaction.py   (NICHT pytest)
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
    import modules.rbac as rbac
    HAVE_DEPS = True
except ImportError as _e:
    HAVE_DEPS = False
    _IMPORT_ERR = str(_e)

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def _fetchall(sql: str, params: tuple = ()):
    conn = await db_manager.get_db()
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


async def run_tests() -> None:
    tmp = tempfile.mkdtemp(prefix="txn_")
    db_path = Path(tmp) / "txn.db"
    await db_manager.init_db(db_path=db_path)

    # Eigene Test-Tabelle (DDL ueber shared Connection)
    conn = await db_manager.get_db()
    await conn.execute("CREATE TABLE IF NOT EXISTS _txntest (id INTEGER PRIMARY KEY AUTOINCREMENT, marker TEXT)")
    await conn.commit()

    try:
        # --- T1: COMMIT bei Erfolg ---
        async with db_manager.transaction() as c:
            await c.execute("INSERT INTO _txntest (marker) VALUES ('t1a')")
            await c.execute("INSERT INTO _txntest (marker) VALUES ('t1b')")
        rows = await _fetchall("SELECT marker FROM _txntest WHERE marker LIKE 't1%'")
        _check("commit_persists", len(rows) == 2, f"n={len(rows)}")

        # --- T2: ROLLBACK bei Exception verwirft die ganze Transaktion ---
        class _Boom(Exception):
            pass
        try:
            async with db_manager.transaction() as c:
                await c.execute("INSERT INTO _txntest (marker) VALUES ('t2x')")
                raise _Boom()
        except _Boom:
            pass
        rows = await _fetchall("SELECT marker FROM _txntest WHERE marker = 't2x'")
        _check("rollback_discards", len(rows) == 0, f"n={len(rows)} (sollte 0)")

        # --- T3: zwei gleichzeitige transaction() serialisieren (kein Interleave) ---
        # Jede Coroutine: DELETE alle + INSERT eigene Marker, mit await dazwischen
        # (erzwingt Loop-Switch falls NICHT lock-serialisiert). Erwartung: am Ende
        # exakt EIN vollstaendiger Marker-Satz (der zuletzt committete), nie gemischt.
        await conn.execute("DELETE FROM _txntest")
        await conn.commit()

        async def writer(marker: str, count: int) -> None:
            async with db_manager.transaction() as c:
                await c.execute("DELETE FROM _txntest")
                for _ in range(count):
                    await c.execute("INSERT INTO _txntest (marker) VALUES (?)", (marker,))
                    await asyncio.sleep(0.005)  # Interleave-Fenster

        await asyncio.gather(writer("A", 3), writer("B", 2))
        rows = await _fetchall("SELECT marker FROM _txntest")
        markers = {r[0] for r in rows}
        ok_single = len(markers) == 1 and (
            (markers == {"A"} and len(rows) == 3) or (markers == {"B"} and len(rows) == 2)
        )
        _check("concurrent_serialized_atomic", ok_single,
               f"markers={markers} n={len(rows)} (erwartet genau 1 vollst. Satz)")

        # --- T4: set_role_grants — zwei Rollen gleichzeitig bleiben isoliert ---
        await asyncio.gather(
            rbac.set_role_grants("111", [("minecraft", "view"), ("minecraft", "control")]),
            rbac.set_role_grants("222", [("satisfactory", "view")]),
        )
        r111 = await _fetchall("SELECT resource, action FROM rbac_role_map WHERE role_id='111' ORDER BY resource, action")
        r222 = await _fetchall("SELECT resource, action FROM rbac_role_map WHERE role_id='222'")
        _check("concurrent_roles_isolated",
               len(r111) == 2 and len(r222) == 1,
               f"r111={len(r111)} r222={len(r222)}")

        # --- T5: Partial-Fail (Abbruch nach DELETE) -> Rollback laesst alte Grants stehen (M39) ---
        await rbac.set_role_grants("333", [("minecraft", "view"), ("satisfactory", "control")])
        before = await _fetchall("SELECT resource, action FROM rbac_role_map WHERE role_id='333'")
        try:
            async with db_manager.transaction() as c:
                await c.execute("DELETE FROM rbac_role_map WHERE role_id='333'")
                raise RuntimeError("simulierter Partial-Fail nach DELETE")
        except RuntimeError:
            pass
        after = await _fetchall("SELECT resource, action FROM rbac_role_map WHERE role_id='333'")
        _check("m39_partial_fail_rollback",
               len(before) == 2 and len(after) == 2,
               f"before={len(before)} after={len(after)} (Rollback muss 2 erhalten)")

    finally:
        await db_manager.close_db()


def main() -> int:
    if not HAVE_DEPS:
        print(f"SKIP: Abhaengigkeiten fehlen lokal ({_IMPORT_ERR}) — Test auf Server laufen lassen.")
        return 0
    asyncio.run(run_tests())
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print("=" * 60)
    for name, ok, msg in _results:
        status = "OK  " if ok else "FAIL"
        print(f"  [{status}] {name}" + (f"  -- {msg}" if msg and not ok else ""))
    print("=" * 60)
    print(f"  ERGEBNIS: {passed}/{total} Checks bestanden")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
