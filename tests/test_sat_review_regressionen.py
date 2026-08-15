#!/usr/bin/env python3
"""
Regressionstests zu den Befunden des Reviews vom 2026-08-14.

Vier Fehler, die der Review über den Mehrinstanz-Umbau gefunden hat. Alle vier
sind still: keiner wirft, keiner loggt einen Fehler, jeder tut einfach etwas
anderes als beabsichtigt. Genau deshalb stehen sie hier als Test und nicht nur
im Bericht.

R1  Die Update-Markierung `_pending_update` steuert den Auto-Installer, und der
    arbeitet auf der ERSTEN Instanz. Sie wurde aber gesetzt, sobald IRGENDEINE
    Instanz ein Update hatte — der Installer wäre über den falschen Server
    gelaufen, hätte dort nichts gefunden und die Markierung gelöscht. Das echte
    Update des anderen Servers wäre damit still verschwunden.

R2  Der tägliche Neustart lief seriell über die Instanzen. Da jeder Lauf einen
    15-Minuten-Countdown abwartet, hätte der zweite Server eine Viertelstunde
    nach der eingestellten Zeit neu gestartet — und der 60-Sekunden-Takt des
    Schedulers wäre doppelt so lange blockiert gewesen.

R3  Bei leerer Server-Registry lieferte `_instanz("IRGENDWAS")` die erste
    Instanz zurück. Der Befehl hätte den einen Server gesteuert und das
    Ergebnis unter dem Namen eines anderen angezeigt.

R4  Nur die erste Instanz bekam `suppress_crash_check`. Ein zweiter Server
    hätte sich während seines eigenen geplanten Neustarts selbst wieder
    hochgefahren — derselbe Fehler, den der Kommentar im Scheduler für den
    ersten Server beschreibt.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_sat_review_regressionen.py
"""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WURZEL = Path(__file__).resolve().parent.parent
_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _quelle(rel: str) -> str:
    return (WURZEL / rel).read_text(encoding="utf-8")


def run_tests() -> None:
    scheduler = _quelle("cogs/scheduler_cog.py")
    cog = _quelle("cogs/satisfactory_cog.py")
    recon = _quelle("bots/recon_bot.py")

    # --- R1: Update-Markierung nur von der ersten Instanz ---
    _check("R1_markierung_nicht_von_irgendeiner",
           "irgendwo_update" not in scheduler,
           "die Sammel-Markierung ist zurück — der Installer liefe über den falschen Server")
    # Diese beiden Pruefungen sicherten frueher den Notbehelf ab, dass der
    # Installer NUR die erste Instanz anfasst und Updates der uebrigen
    # lediglich gemeldet werden. Seit 2026-08-15 installiert er auf jeder
    # Instanz — die alte Erwartung waere jetzt ein Rueckschritt, deshalb
    # pruefen sie das neue Verhalten.
    _check("R1_markierung_je_instanz",
           "self._pending_setzen(_sid, _offen)" in scheduler,
           "jede Instanz traegt ihre eigene Update-Markierung")
    _check("R1_installer_laeuft_je_instanz",
           "async def _auto_update_einer" in scheduler
           and "for sid in list(self.sat_instanzen):" in scheduler,
           "der Installer arbeitet jede Instanz mit ausstehendem Update ab")

    # --- R2: Neustarts laufen parallel ---
    _check("R2_neustart_parallel",
           "asyncio.gather(" in scheduler and "_daily_restart_einer(sid, now)" in scheduler,
           "seriell wartet der zweite Server einen ganzen Countdown zu lange")
    _check("R2_ein_fehler_reisst_nicht_mit",
           "return_exceptions=True" in scheduler,
           "ein Fehlschlag bei einer Instanz darf die andere nicht verhindern")

    # --- R3: unbekannte Angabe wird nicht stillschweigend umgebogen ---
    baum = ast.parse(cog)
    instanz_fn = next(
        (k for k in ast.walk(baum)
         if isinstance(k, ast.FunctionDef) and k.name == "_instanz"), None)
    _check("R3_instanz_existiert", instanz_fn is not None)
    if instanz_fn:
        rumpf = ast.unparse(instanz_fn)
        # Im Zweig "Registry leer" muss eine ausdrückliche Server-Angabe
        # zu (None, None, None) führen statt zur ersten Instanz.
        _check("R3_leere_registry_meldet_unbekannt",
               "if server:" in rumpf and "return (None, None, None)" in rumpf,
               "mit Angabe darf bei leerer Registry nicht die erste Instanz kommen")

    # --- R4: Schutzschaltung für jede Instanz ---
    _check("R4_suppress_fuer_alle",
           "for _hc_sid, _hc in sat_health_checkers.items():" in recon
           and "_hc.suppress_crash_check" in recon,
           "ohne das startet ein zweiter Server sich im eigenen Wartungsfenster neu")
    _check("R4_eigener_schluessel",
           'is_suppressed("sat", _s.lower())' in recon,
           "der Schlüssel muss zu dem passen, den der Scheduler setzt")

    # Der Scheduler muss denselben Schlüssel benutzen — sonst greift die
    # Unterdrückung ins Leere, und genau das fiele niemandem auf.
    _check("R4_scheduler_nutzt_denselben_schluessel",
           'har.suppress("sat", sid.lower(), duration_seconds=900)' in scheduler,
           "Scheduler und Health-Check müssen denselben Schlüssel benutzen")


def main() -> int:
    print("=" * 60)
    print("  Regressionen aus dem Review vom 2026-08-14")
    print("=" * 60)

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
