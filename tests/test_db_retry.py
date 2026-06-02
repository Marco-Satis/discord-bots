#!/usr/bin/env python3
"""
Unit-Test fuer den Write-Retry-Pfad in DBHelper.execute.

Prueft ohne echte Datenbank (Mock-Connection):
  1. Retry-dann-Erfolg: execute wirft 1x "database is locked", dann ok
     -> execute 2x, commit 1x, lastrowid zurueck
  2. Commit-Fehler re-executet NICHT: execute ok, commit wirft "locked"
     -> execute genau 1x (kein Doppel-Write), Fehler propagiert
  3. Nicht-Lock-OperationalError: sofort durchgereicht, kein Retry
  4. Retries erschoepft: execute wirft immer "locked"
     -> nach _WRITE_RETRIES Versuchen Fehler, commit nie

Ausfuehren:  python tests/test_db_retry.py
"""

import asyncio
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.database import db_manager  # noqa: E402
from modules.database.db_manager import DBHelper  # noqa: E402

LOCKED = sqlite3.OperationalError("database is locked")


class FakeCursor:
    def __init__(self, lastrowid: int = 42) -> None:
        self.lastrowid = lastrowid


def make_conn(execute_side_effect, commit_side_effect=None) -> AsyncMock:
    """Baut eine Mock-aiosqlite-Connection mit async execute/commit."""
    conn = AsyncMock()
    conn.execute = AsyncMock(side_effect=execute_side_effect)
    conn.commit = AsyncMock(side_effect=commit_side_effect)
    return conn


async def case_retry_then_success() -> None:
    cur = FakeCursor(7)
    conn = make_conn(execute_side_effect=[LOCKED, cur])
    with patch.object(db_manager.asyncio, "sleep", new=AsyncMock()):
        result = await DBHelper(conn).execute("INSERT INTO t VALUES (?)", (1,))
    assert conn.execute.call_count == 2, f"execute {conn.execute.call_count}x, erwartet 2"
    assert conn.commit.call_count == 1, f"commit {conn.commit.call_count}x, erwartet 1"
    assert result == 7, f"lastrowid {result}, erwartet 7"


async def case_commit_fail_no_reexecute() -> None:
    cur = FakeCursor()
    conn = make_conn(execute_side_effect=[cur], commit_side_effect=LOCKED)
    raised = False
    with patch.object(db_manager.asyncio, "sleep", new=AsyncMock()):
        try:
            await DBHelper(conn).execute("UPDATE t SET x=x+1", ())
        except sqlite3.OperationalError:
            raised = True
    assert raised, "commit-Fehler haette propagieren muessen"
    assert conn.execute.call_count == 1, (
        f"execute {conn.execute.call_count}x — Doppel-Write-Risiko! erwartet 1"
    )


async def case_non_lock_error_propagates() -> None:
    err = sqlite3.OperationalError("no such table: t")
    conn = make_conn(execute_side_effect=err)
    raised = False
    with patch.object(db_manager.asyncio, "sleep", new=AsyncMock()):
        try:
            await DBHelper(conn).execute("INSERT INTO t VALUES (?)", (1,))
        except sqlite3.OperationalError as e:
            raised = "no such table" in str(e)
    assert raised, "Nicht-Lock-Fehler haette sofort propagieren muessen"
    assert conn.execute.call_count == 1, f"execute {conn.execute.call_count}x, erwartet 1 (kein Retry)"
    assert conn.commit.call_count == 0, "commit haette nicht laufen duerfen"


async def case_retries_exhausted() -> None:
    conn = make_conn(execute_side_effect=LOCKED)
    raised = False
    with patch.object(db_manager.asyncio, "sleep", new=AsyncMock()):
        try:
            await DBHelper(conn).execute("INSERT INTO t VALUES (?)", (1,))
        except sqlite3.OperationalError:
            raised = True
    assert raised, "erschoepfte Retries haetten Fehler werfen muessen"
    assert conn.execute.call_count == db_manager._WRITE_RETRIES, (
        f"execute {conn.execute.call_count}x, erwartet {db_manager._WRITE_RETRIES}"
    )
    assert conn.commit.call_count == 0, "commit haette nie laufen duerfen"


CASES = [
    ("Retry-dann-Erfolg", case_retry_then_success),
    ("Commit-Fehler re-executet NICHT", case_commit_fail_no_reexecute),
    ("Nicht-Lock-Fehler sofort", case_non_lock_error_propagates),
    ("Retries erschoepft", case_retries_exhausted),
]


async def main() -> int:
    print("=" * 64)
    print("  DBHelper.execute — Write-Retry-Pfad")
    print("=" * 64)
    failed = 0
    for name, fn in CASES:
        try:
            await fn()
            print(f"  [OK]   {name}")
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {name}: unerwartet {type(e).__name__}: {e}")
    print("-" * 64)
    if failed:
        print(f"  ERGEBNIS: {failed} von {len(CASES)} Faellen fehlgeschlagen.")
        return 1
    print(f"  ERGEBNIS: Alle {len(CASES)} Faelle bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
