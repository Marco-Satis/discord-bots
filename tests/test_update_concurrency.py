#!/usr/bin/env python3
"""
Regressions-Test: perform_update darf nicht zweimal parallel laufen.

Am 2026-08-11 haben Scheduler-Auto-Update (18:59:16) und `/update start`
aus Discord (18:59:38) zwei SteamCMD-Prozesse auf dasselbe Install-
Verzeichnis losgelassen. Folge im Log:
"Server Start nach Update fehlgeschlagen: Server laeuft bereits."

Der Test ersetzt den eigentlichen Update-Lauf durch eine langsame Attrappe
und prueft, dass ein zweiter Aufruf abgelehnt wird statt mitzulaufen.
"""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# utils.logger stubben — der Dev-Mirror hat die Bot-Dependencies nicht
_utils = types.ModuleType("utils")
_logger_mod = types.ModuleType("utils.logger")


class _NullLogger:
    def __getattr__(self, _name):
        return lambda *a, **k: None


_logger_mod.get_logger = lambda *_a, **_k: _NullLogger()
_utils.logger = _logger_mod
sys.modules.setdefault("utils", _utils)
sys.modules["utils.logger"] = _logger_mod

_spec = importlib.util.spec_from_file_location(
    "update_checker", PROJECT_ROOT / "modules/monitoring/update_checker.py"
)
_uc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_uc)

failures: list[str] = []


def check(bedingung: bool, beschreibung: str) -> None:
    print(f"  {'OK   ' if bedingung else 'FEHLT'} {beschreibung}")
    if not bedingung:
        failures.append(beschreibung)


async def run() -> None:
    checker = _uc.UpdateChecker()
    laeufe: list[str] = []

    async def fake_locked(server=None, har=None):
        laeufe.append("start")
        await asyncio.sleep(0.3)
        return True, "fertig"

    checker._perform_update_locked = fake_locked

    erster = asyncio.create_task(checker.perform_update())
    await asyncio.sleep(0.05)  # sicherstellen, dass der erste den Lock haelt
    ok2, msg2 = await checker.perform_update()
    ok1, _msg1 = await erster

    check(ok1 is True, "Erster Update-Lauf geht durch")
    check(ok2 is False, "Zweiter paralleler Lauf wird abgelehnt")
    check("bereits" in msg2.lower(), f"Absage nennt den Grund ({msg2!r})")
    check(len(laeufe) == 1, f"Nur EIN echter Lauf gestartet (waren: {len(laeufe)})")

    # Nach Abschluss muss ein neuer Lauf wieder erlaubt sein
    ok3, _ = await checker.perform_update()
    check(ok3 is True, "Nach Abschluss ist der naechste Lauf wieder moeglich")


def main() -> int:
    print("=" * 70)
    print("  update_checker — Parallel-Lauf-Schutz")
    print("=" * 70)
    asyncio.run(run())
    print("-" * 70)
    if failures:
        print(f"  ERGEBNIS: {len(failures)} Pruefung(en) fehlgeschlagen")
        return 1
    print("  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
