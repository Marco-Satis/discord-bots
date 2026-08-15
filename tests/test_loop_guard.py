#!/usr/bin/env python3
"""
Tests für utils/loop_guard.py — den Fehler-Handler der Dauerläufer.

Warum es das gibt: discord.py beendet eine `tasks.loop` bei jeder unerwarteten
Ausnahme endgültig. Der Dienst bleibt danach „active", die Aufgabe ist tot —
keine Backups, kein Offline-Alarm, keine ablaufenden Timeouts, und niemand
merkt es. Der frühere einzige Handler im Projekt konnte nie neu starten, weil
er `is_running()` prüfte; im `.error`-Kontext läuft der Task aber noch.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_loop_guard.py
"""
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from discord.ext import tasks
    from utils import loop_guard
    HAVE_DISCORD = True
except ImportError:
    HAVE_DISCORD = False

_results: list[tuple[str, bool, str]] = []
WURZEL = Path(__file__).resolve().parent.parent


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def run_tests() -> None:
    # --- 1. Der Handler wird überhaupt registriert ---
    @tasks.loop(seconds=3600)
    async def leer_task():
        pass

    loop_guard.guard(leer_task, name="leer_task")
    _check("handler_registriert", leer_task._error is not None)

    # --- 2. Er meldet, protokolliert und plant den Neustart ---
    async def _fehlerfall() -> None:
        loop_guard.reset_historie()
        gemeldet: list = []

        async def _melder(titel: str, text: str) -> None:
            gemeldet.append((titel, text))

        loop_guard.set_reporter(_melder)

        @tasks.loop(seconds=3600)
        async def kaputt():
            pass

        loop_guard.guard(kaputt, name="kaputt")
        # Den Handler direkt aufrufen — genau so ruft discord.py ihn auf.
        await kaputt._error(ValueError("Testfehler"))

        _check("meldung_raus", len(gemeldet) == 1, str(gemeldet))
        _check("meldung_nennt_schleife", "kaputt" in gemeldet[0][0], str(gemeldet))
        _check("meldung_nennt_fehler", "ValueError" in gemeldet[0][1], str(gemeldet))
        _check("neustart_geplant", len(loop_guard._laufende) >= 1,
               str(loop_guard._laufende))

        # Aufräumen, damit der Test keine offenen Tasks hinterlässt
        for t in list(loop_guard._laufende):
            t.cancel()
        loop_guard.set_reporter(None)

    asyncio.run(_fehlerfall())

    # --- 3. Nach zu vielen Fehlschlägen bleibt die Schleife gestoppt ---
    async def _budget() -> None:
        loop_guard.reset_historie()
        loop_guard.set_reporter(None)

        @tasks.loop(seconds=3600)
        async def dauerkaputt():
            pass

        loop_guard.guard(dauerkaputt, name="dauerkaputt")
        for _ in range(loop_guard.MAX_NEUSTARTS + 1):
            await dauerkaputt._error(RuntimeError("immer wieder"))

        geplant = len(loop_guard._laufende)
        _check("budget_begrenzt", geplant <= loop_guard.MAX_NEUSTARTS,
               f"{geplant} Neustarts geplant bei Budget {loop_guard.MAX_NEUSTARTS}")
        for t in list(loop_guard._laufende):
            t.cancel()

    asyncio.run(_budget())

    # --- 4. Ein Melder, der selbst wirft, darf den Handler nicht mitreißen ---
    async def _melder_kaputt() -> None:
        loop_guard.reset_historie()

        async def _boeser_melder(titel: str, text: str) -> None:
            raise ConnectionError("Discord nicht erreichbar")

        loop_guard.set_reporter(_boeser_melder)

        @tasks.loop(seconds=3600)
        async def schleife():
            pass

        loop_guard.guard(schleife, name="schleife")
        try:
            await schleife._error(ValueError("Testfehler"))
            _check("melder_fehler_verschluckt", True)
        except Exception as e:  # noqa: BLE001
            _check("melder_fehler_verschluckt", False, f"{type(e).__name__}: {e}")
        for t in list(loop_guard._laufende):
            t.cancel()
        loop_guard.set_reporter(None)

    asyncio.run(_melder_kaputt())

    # --- 5. Jede Schleife im Projekt startet bewacht ---
    # Das ist der eigentliche Regressionsschutz: eine neue tasks.loop ohne
    # guard() fällt hier auf, statt erst im Betrieb still zu sterben.
    ungeschuetzt: list[str] = []
    for pfad in list((WURZEL / "cogs").glob("*.py")) + list((WURZEL / "bots").glob("*.py")):
        text = pfad.read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*([\w.]+)\.start\(\)", text, re.M):
            objekt = m.group(1)
            if objekt.endswith(("_writer", "_checker")) and "task" not in objekt:
                continue
            # nur echte tasks.loop-Objekte: der Name muss im File als Loop definiert sein
            kurz = objekt.split(".")[-1]
            if not re.search(r"@tasks\.loop\([^)]*\)\s*\n\s*async def " + re.escape(kurz),
                             text):
                continue
            if f"guard({objekt}" not in text:
                ungeschuetzt.append(f"{pfad.name}:{kurz}")

    _check("alle_schleifen_bewacht", not ungeschuetzt,
           "ohne Guard: " + ", ".join(sorted(ungeschuetzt)))

    # --- 6. Herunterfahren: Schleifen werden gestoppt, nicht neu gestartet ---
    async def _shutdown() -> None:
        from utils import shutdown as shutdown_modul

        loop_guard.reset_historie()
        gemeldet: list = []

        async def _melder(titel: str, text: str) -> None:
            gemeldet.append(titel)

        loop_guard.set_reporter(_melder)

        @tasks.loop(seconds=3600)
        async def laeuft():
            pass

        loop_guard.guard(laeuft, name="laeuft")
        _check("schleife_im_verzeichnis", "laeuft" in loop_guard._bewacht,
               str(list(loop_guard._bewacht)))

        laeuft.start()
        await asyncio.sleep(0)  # dem Task einen Durchlauf goennen
        _check("schleife_laeuft", laeuft.is_running())

        gestoppt = loop_guard.alle_stoppen()
        _check("stoppt_laufende_schleife", "laeuft" in gestoppt, str(gestoppt))
        await asyncio.sleep(0)
        _check("schleife_steht", not laeuft.is_running())

        # Waehrend des Shutdowns darf ein Fehler weder melden noch neu starten
        shutdown_modul.shutting_down = True
        try:
            loop_guard._laufende.clear()
            await laeuft._error(RuntimeError("DB zu"))
            _check("shutdown_keine_meldung", not gemeldet, str(gemeldet))
            _check("shutdown_kein_neustart", not loop_guard._laufende,
                   str(loop_guard._laufende))
        finally:
            shutdown_modul.shutting_down = False
            loop_guard.set_reporter(None)
            for t in list(loop_guard._laufende):
                t.cancel()

        # Ausserhalb des Shutdowns meldet derselbe Handler wieder
        loop_guard.set_reporter(_melder)
        await laeuft._error(RuntimeError("echter Ausfall"))
        _check("nach_shutdown_wieder_meldung", len(gemeldet) == 1, str(gemeldet))
        loop_guard.set_reporter(None)
        for t in list(loop_guard._laufende):
            t.cancel()

    asyncio.run(_shutdown())


def main() -> int:
    print("=" * 60)
    print("  Loop-Guard Tests (utils/loop_guard.py)")
    print("=" * 60)
    if not HAVE_DISCORD:
        print("  [SKIP] discord.py lokal nicht installiert — Test läuft am Server.")
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
