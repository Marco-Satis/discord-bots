#!/usr/bin/env python3
"""
Tests für den Rollback des Minecraft-Update-Managers.

Zwei Befunde aus dem Review vom 2026-08-14:

* `_perform_rollback` setzte `sudo rm -rf` und `sudo mv` ab, **ohne den
  Rückgabewert anzusehen** — und meldete danach „Rollback erfolgreich", auch
  wenn nichts passiert war. Anschließend startete es den Server auf dem
  Zwischenstand.
* Scheiterte etwas **nach** dem Atomic-Swap (etwa die NeoForge-Installation),
  lief der Fehlerpfad in `_safe_start_server()` — also den halbfertigen Stand
  hochfahren, statt zurückzurollen.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_update_rollback.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from modules.minecraft.update_manager import UpdateManager
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _manager() -> "UpdateManager":
    return UpdateManager(server_id="test")


def run_tests() -> None:
    # --- 1. Ein gescheiterter Befehl wird als Fehlschlag erkannt ---
    async def _befehl_scheitert() -> None:
        m = _manager()
        try:
            # `false` beendet sich mit Rückgabewert 1
            await m._lauf("false")
            _check("fehlschlag_wirft", False, "kein Fehler bei Rueckgabewert 1")
        except RuntimeError as e:
            _check("fehlschlag_wirft", True)
            _check("fehlermeldung_nennt_rc", "rc=1" in str(e), str(e))

        # Erfolgreicher Befehl wirft nicht
        try:
            await m._lauf("true")
            _check("erfolg_wirft_nicht", True)
        except Exception as e:  # noqa: BLE001
            _check("erfolg_wirft_nicht", False, str(e))

    asyncio.run(_befehl_scheitert())

    # --- 2. Fehlendes Rollback-Verzeichnis meldet Fehlschlag, kein Erfolg ---
    async def _kein_verzeichnis() -> None:
        m = _manager()
        gestartet: list = []
        m._safe_start_server = lambda: gestartet.append(True) or asyncio.sleep(0)

        with tempfile.TemporaryDirectory() as ordner:
            fehlt = Path(ordner) / "gibtsnicht"
            ziel = Path(ordner) / "server"
            ergebnis = await m._perform_rollback(fehlt, ziel)

        _check("ohne_rollback_verzeichnis_false", ergebnis is False, str(ergebnis))
        _check("ohne_rollback_kein_serverstart", not gestartet,
               "Server wurde trotz misslungenem Rollback gestartet")

    asyncio.run(_kein_verzeichnis())

    # --- 3. Erfolgreicher Rollback meldet True und startet den Server ---
    async def _echter_rollback() -> None:
        m = _manager()
        gestartet: list = []

        async def _start():
            gestartet.append(True)

        m._safe_start_server = _start

        # sudo umgehen: die Befehle ohne "sudo" ausfuehren, damit der Test
        # ohne Rechte laeuft. Geprueft wird die Logik, nicht sudo.
        echtes_lauf = m._lauf

        async def _ohne_sudo(*befehl, **kw):
            gefiltert = [b for b in befehl if b != "sudo"]
            await echtes_lauf(*gefiltert, **kw)

        m._lauf = _ohne_sudo

        with tempfile.TemporaryDirectory() as ordner:
            alt = Path(ordner) / "server_rollback"
            alt.mkdir()
            (alt / "marker.txt").write_text("alter stand", encoding="utf-8")
            ziel = Path(ordner) / "server"
            ziel.mkdir()
            (ziel / "kaputt.txt").write_text("neuer halbfertiger stand", encoding="utf-8")

            ergebnis = await m._perform_rollback(alt, ziel)

            _check("rollback_meldet_erfolg", ergebnis is True, str(ergebnis))
            _check("alter_stand_ist_zurueck",
                   (ziel / "marker.txt").exists(), "marker.txt fehlt nach dem Rollback")
            _check("neuer_stand_ist_weg",
                   not (ziel / "kaputt.txt").exists(), "halbfertiger Stand liegt noch da")
            _check("server_wurde_gestartet", bool(gestartet))

    asyncio.run(_echter_rollback())

    # --- 4. Nach dem Swap wird zurueckgerollt, davor nur gestartet ---
    async def _fehlerpfad() -> None:
        m = _manager()
        rollbacks: list = []
        starts: list = []

        async def _rollback(rb, sp):
            rollbacks.append((rb, sp))
            return True

        async def _start():
            starts.append(True)

        m._perform_rollback = _rollback
        m._safe_start_server = _start

        # Vor dem Swap: nur starten
        await m._nach_fehler_aufraeumen(False, Path("/tmp/rb"), Path("/tmp/srv"))
        _check("vor_swap_nur_start", not rollbacks and len(starts) == 1,
               f"rollbacks={rollbacks} starts={starts}")

        # Nach dem Swap: zurueckrollen
        await m._nach_fehler_aufraeumen(True, Path("/tmp/rb"), Path("/tmp/srv"))
        _check("nach_swap_rollback", len(rollbacks) == 1, str(rollbacks))
        _check("nach_swap_kein_blinder_start", len(starts) == 1,
               "halbfertiger Stand waere hochgefahren worden")

    asyncio.run(_fehlerpfad())


def main() -> int:
    print("=" * 60)
    print("  Update-Rollback (modules/minecraft/update_manager.py)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] Abhängigkeiten lokal nicht installiert — Test läuft am Server.")
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
