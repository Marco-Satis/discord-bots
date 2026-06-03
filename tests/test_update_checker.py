#!/usr/bin/env python3
"""
Regressions-Tests fuer UpdateChecker — schuetzt die SAT-Update-Fixes
(Notify-Dedup gegen 3x-Spam + L1 manual_stop-Clear-Guard).

Lauf: python tests/test_update_checker.py
Reine Logik-Tests mit Mocks (kein steamcmd/systemd, plattform-unabhaengig).
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.monitoring.update_checker import UpdateChecker  # noqa: E402

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


# --------------------------------------------------------------------------
# Test 1: Notify-Dedup — on_update_available feuert nur bei NEUER Build-ID,
# nicht bei jedem check() (verhindert 3x-Update-verfuegbar-Spam).
# --------------------------------------------------------------------------
def test_notify_dedup() -> None:
    uc = UpdateChecker()
    calls: list[tuple] = []

    async def cb(installed, available):
        calls.append((installed, available))

    uc.on_update_available = cb

    async def run_check(installed, available):
        with patch.object(uc, "_get_installed_buildid",
                          new=AsyncMock(return_value=installed)), \
             patch.object(uc, "_get_available_buildid",
                          new=AsyncMock(return_value=available)):
            return await uc.check()

    # 2x gleicher verfuegbarer Build (installed != available) -> nur 1 Notify
    asyncio.run(run_check("100", "200"))
    asyncio.run(run_check("100", "200"))
    _check("dedup_einmal_pro_build", len(calls) == 1,
           f"erwartet 1 Notify nach 2 gleichen Checks, war {len(calls)}")

    # Neuer verfuegbarer Build -> 2. Notify (State-Change)
    asyncio.run(run_check("100", "300"))
    _check("neuer_build_notifyt", len(calls) == 2,
           f"erwartet 2 nach neuem Build, war {len(calls)}")

    # Kein Update (installed == available) -> reset, kein Notify
    asyncio.run(run_check("300", "300"))
    _check("kein_update_kein_notify", len(calls) == 2,
           f"erwartet weiterhin 2, war {len(calls)}")
    _check("notified_reset", uc._notified_buildid is None,
           "_notified_buildid muss bei 'kein Update' zurueckgesetzt sein")

    # Nach Reset: erneut Update verfuegbar -> notifyt wieder (State-Change)
    asyncio.run(run_check("300", "400"))
    _check("notify_nach_reset", len(calls) == 3,
           f"erwartet 3 nach Reset+neuem Build, war {len(calls)}")


# --------------------------------------------------------------------------
# Test 2: L1 — _safe_start cleart den manual_stop-Marker NUR wenn dieses
# Update ihn selbst gesetzt hat (_update_marked_stop). Sonst wuerde ein
# manuell von Marco gesetzter Stop-Marker faelschlich geloescht.
# --------------------------------------------------------------------------
def test_safe_start_clear_guard() -> None:
    # Fall A: Flag False -> KEIN Clear (fremder/manueller Marker bleibt)
    uc = UpdateChecker()
    uc._update_marked_stop = False
    with patch("modules.monitoring.manual_stop_state.mark_started",
               new=AsyncMock()) as mk:
        asyncio.run(uc._safe_start(None))  # server=None -> nur Clear-Logik
        _check("flag_false_kein_clear", mk.call_count == 0,
               f"mark_started darf NICHT laufen wenn Flag False, war {mk.call_count}x")

    # Fall B: Flag True -> Clear + Flag-Reset
    uc2 = UpdateChecker()
    uc2._update_marked_stop = True
    with patch("modules.monitoring.manual_stop_state.mark_started",
               new=AsyncMock()) as mk2:
        asyncio.run(uc2._safe_start(None))
        _check("flag_true_clear", mk2.call_count == 1,
               f"mark_started muss 1x laufen wenn Flag True, war {mk2.call_count}x")
    _check("flag_reset_nach_clear", uc2._update_marked_stop is False,
           "_update_marked_stop muss nach Clear False sein")


def main() -> int:
    print("=" * 70)
    print("  UpdateChecker Regressions-Tests (Dedup + L1 manual_stop-Guard)")
    print("=" * 70)
    for fn in (test_notify_dedup, test_safe_start_clear_guard):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _check(fn.__name__, False, f"Exception: {e}")

    failed = 0
    for name, ok, msg in _results:
        status = "[OK]  " if ok else "[FAIL]"
        line = f"  {status} {name}"
        if not ok and msg:
            line += f"  -> {msg}"
        print(line)
        if not ok:
            failed += 1

    print("-" * 70)
    if failed == 0:
        print(f"  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN ({len(_results)} Checks)")
        return 0
    print(f"  ERGEBNIS: {failed}/{len(_results)} FEHLGESCHLAGEN")
    return 1


if __name__ == "__main__":
    sys.exit(main())
