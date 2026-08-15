#!/usr/bin/env python3
"""Test: Modpack-Update ohne Server-Pack meldet genau einmal und versucht nichts.

Prueft den Preflight in SchedulerCog._check_mc_modpack_auto_update:
  - kein run_update()-Versuch
  - keine Fehlversuchs-Zaehlung (der Loop-Guard wird nicht verbrannt)
  - genau EINE Benachrichtigung pro Ziel-Version, auch ueber mehrere Checks
  - eine neuere Version wird wieder normal gemeldet
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cogs.scheduler_cog import SchedulerCog  # noqa: E402


class _StubUpdateManager:
    """Liefert ein Update, dessen version_info kein server_pack enthaelt."""

    def __init__(self, latest: str, file_id: int):
        self.latest = latest
        self.file_id = file_id
        self.run_update_calls = 0

    async def check_for_update(self):
        return True, {
            "current": "v51",
            "current_file_id": 8339512,
            "latest": self.latest,
            "latest_file_id": self.file_id,
            "server_pack": None,
        }

    async def run_update(self, **kwargs):
        self.run_update_calls += 1
        return False, {"error": "Kein Server Pack für diese Version verfügbar"}


def _make_cog(mgr) -> SchedulerCog:
    bot = SimpleNamespace(
        config={},
        mc_update_managers={"BMC": mgr},
        mc_servers={},
        notifier=None,
    )
    cog = SchedulerCog.__new__(SchedulerCog)
    cog.bot = bot
    # Nur die Felder setzen, die der getestete Pfad braucht.
    cog._mc_modpack_check_hours = [12, 0]
    cog._mc_modpack_immediate_hour = 12
    cog._mc_modpack_daily_restart_hour = 4
    cog._mc_last_modpack_auto_check = {}
    cog._mc_update_fail_count = {}
    cog._mc_update_fail_target = {}
    cog._mc_update_giveup_version = {}
    cog._mc_update_max_attempts = 2
    cog._mc_update_blocked_version = {}
    cog._mc_blocked_state_loaded = True
    return cog


async def _run() -> bool:
    mgr = _StubUpdateManager("v52.", 8598260)
    cog = _make_cog(mgr)
    notify = AsyncMock()
    db_writes = []

    async def _fake_db_mark(self, server_id, target, version_info):
        db_writes.append((server_id, target))

    ok = True
    with patch.object(SchedulerCog, "notifier", property(lambda self: notify)), \
         patch.object(SchedulerCog, "_mark_modpack_blocked_in_db", _fake_db_mark):
        # Drei Checks an drei Tagen — muss genau eine Meldung ergeben.
        for day in (12, 13, 14):
            cog._mc_last_modpack_auto_check = {}
            await cog._check_mc_modpack_auto_update(
                datetime(2026, 8, day, 12, 0), "BMC"
            )

        if mgr.run_update_calls != 0:
            print(f"  FEHLER: run_update {mgr.run_update_calls}x aufgerufen (erwartet 0)")
            ok = False
        if cog._mc_update_fail_count.get("BMC"):
            print(f"  FEHLER: Fehlversuchs-Zaehler {cog._mc_update_fail_count} (erwartet leer)")
            ok = False
        if notify.send_admin.await_count != 1:
            print(f"  FEHLER: {notify.send_admin.await_count} Benachrichtigungen (erwartet 1)")
            ok = False
        if db_writes != [("BMC", "v52.")]:
            print(f"  FEHLER: DB-Writes {db_writes} (erwartet genau einer)")
            ok = False

        # Neuere Version -> wieder genau eine zusaetzliche Meldung.
        mgr.latest, mgr.file_id = "v53.", 8600000
        cog._mc_last_modpack_auto_check = {}
        await cog._check_mc_modpack_auto_update(datetime(2026, 8, 15, 12, 0), "BMC")
        if notify.send_admin.await_count != 2:
            print(
                f"  FEHLER: nach neuer Version {notify.send_admin.await_count} "
                f"Benachrichtigungen (erwartet 2)"
            )
            ok = False

    return ok


def main() -> int:
    print("=" * 62)
    print("  Modpack-Update ohne Server-Pack")
    print("=" * 62)
    ok = asyncio.run(_run())
    print()
    if ok:
        print("  ERGEBNIS: BESTANDEN")
        return 0
    print("  ERGEBNIS: FEHLGESCHLAGEN")
    return 1


if __name__ == "__main__":
    sys.exit(main())
