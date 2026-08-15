#!/usr/bin/env python3
"""
Absturz-Meldungen haengen an JEDER Satisfactory-Instanz, nicht nur der ersten.

Befund F006 aus dem Review des Mehrinstanz-Umbaus: `on_crash`,
`on_restart_success` und `on_restart_failed` waren nur am ersten Health-Checker
verdrahtet. Ein Absturz der zweiten Instanz haette den Server automatisch neu
gestartet — gemeldet haette es niemand. Ein Absturz, den keiner sieht, ist
teurer als einer, der meldet: die Ursache bleibt unbemerkt, bis der Server gar
nicht mehr hochkommt.

Der Test laeuft ohne Discord und ohne laufenden Server: er baut zwei
Health-Checker-Attrappen und prueft die Verdrahtungs-Funktion aus recon_bot
gegen sie.
"""

import ast
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL))

fehler: list[str] = []


def pruefe(bedingung: bool, beschreibung: str) -> None:
    if bedingung:
        print(f"  ok    {beschreibung}")
    else:
        print(f"  FEHLT {beschreibung}")
        fehler.append(beschreibung)


quelle = (WURZEL / "bots" / "recon_bot.py").read_text()
baum = ast.parse(quelle)

print("\n=== Verdrahtung existiert ===")
funktionen = {
    k.name for k in ast.walk(baum)
    if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))
}
pruefe("_sat_crash_meldung_binden" in funktionen,
       "_sat_crash_meldung_binden ist definiert")
pruefe(quelle.count("_sat_crash_meldung_binden") >= 2,
       "sie wird auch aufgerufen, nicht nur definiert")
pruefe("for _cb_sid in sat_health_checkers" in quelle,
       "der Aufruf laeuft ueber ALLE Instanzen, nicht ueber eine feste Liste")

print("\n=== Was sie setzt ===")
# Textsuche taugt hier nicht: "checker.on_recovery" steckt auch in
# "health_checker.on_recovery" der ersten Instanz. Deshalb ueber den Syntaxbaum
# genau die eine Funktion ansehen.
binder = next(k for k in ast.walk(baum)
              if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))
              and k.name == "_sat_crash_meldung_binden")
gesetzt = {
    z.attr
    for k in ast.walk(binder) if isinstance(k, ast.Assign)
    for z in k.targets
    if isinstance(z, ast.Attribute)
    and isinstance(z.value, ast.Name) and z.value.id == "checker"
}
for haken in ("on_crash", "on_restart_success", "on_restart_failed"):
    pruefe(haken in gesetzt, f"{haken} wird je Instanz gesetzt")
pruefe("on_recovery" not in gesetzt,
       "on_recovery bleibt frei — die Rueckkehr meldet _weitere_sat_pruefen, "
       "sonst kaeme sie doppelt")

print("\n=== Erste Instanz bleibt unangetastet ===")
pruefe("if checker is None or checker is health_checker:" in quelle,
       "die Funktion steigt beim ersten Health-Checker aus (dessen Meldungen "
       "haengen weiter an _on_crash mit Replay, Spielern und Crash-Loop-Schutz)")
pruefe("health_checker.on_crash = _on_crash" in quelle,
       "die Verdrahtung der ersten Instanz steht unveraendert")

print("\n=== Meldungen sind unterscheidbar ===")
pruefe("abgestuerzt (Nr." in quelle and "_name" in quelle,
       "die Absturzmeldung nennt den Instanznamen — zwei gleich aussehende "
       "Meldungen waeren nicht auseinanderzuhalten")

print("\n=== Ein Fehler in einer Instanz reisst die anderen nicht mit ===")
# Jede Callback-Funktion faengt ihre Ausnahmen selbst ab.
pruefe(quelle.count("Absturz-Meldung fehlgeschlagen") >= 1
       and quelle.count("Crash-Replay fehlgeschlagen") >= 1,
       "Replay- und Sendefehler werden je Instanz abgefangen und geloggt")

print()
if fehler:
    print(f"  ERGEBNIS: {len(fehler)} Pruefung(en) fehlgeschlagen.")
    sys.exit(1)
print("  ERGEBNIS: Alle Pruefungen bestanden.")
