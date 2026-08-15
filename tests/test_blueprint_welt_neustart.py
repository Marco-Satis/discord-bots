#!/usr/bin/env python3
"""
Aktive Welt kommt vom Server, und ein Upload startet den Neustart selbst.

Zwei Befunde vom 2026-08-15:

1. Die aktive Welt wurde aus Dateizeiten geraten ("neuester Ordner gewinnt").
   Der Blueprint-Sync schreibt aber selbst in einen Welt-Ordner und setzt damit
   dessen Aenderungszeit — die Erkennung bestaetigte sich also selbst. Sie
   sprang von `FactorySatis` auf `BoberKurwa`, waehrend der Server unveraendert
   FactorySatis spielte.

2. Nach einem Blueprint-Upload erschien nur ein Knopf. Wurde er nicht
   gedrueckt, blieb der Upload wirkungslos: der Server liest Blaupausen nur
   beim Start ein. Belegt: fuenf Blaupausen um 08:49 hochgeladen, Server lief
   bis 14:00 unveraendert weiter.
"""

import ast
import sys
import tempfile
import time
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL))

from modules.satisfactory.blueprint_manager import BlueprintManager  # noqa: E402

fehler: list[str] = []


def pruefe(bedingung: bool, beschreibung: str) -> None:
    if bedingung:
        print(f"  ok    {beschreibung}")
    else:
        print(f"  FEHLT {beschreibung}")
        fehler.append(beschreibung)


def welten_anlegen(basis: Path, namen) -> None:
    for n in namen:
        (basis / n).mkdir(parents=True, exist_ok=True)
        time.sleep(0.01)     # unterschiedliche Aenderungszeiten erzwingen


print("\n=== Aktive Welt ===")
with tempfile.TemporaryDirectory() as t:
    basis = Path(t) / "blueprints"
    welten_anlegen(basis, ["FactorySatis", "Kreativ", "BoberKurwa"])
    juengster = max(basis.iterdir(), key=lambda d: d.stat().st_mtime).name
    pruefe(juengster == "BoberKurwa", "Testaufbau: juengster Ordner ist BoberKurwa")

    # Das ist der Kern: der Server sagt FactorySatis, das Dateisystem BoberKurwa.
    mit = BlueprintManager(blueprint_path=basis, session_provider=lambda: "FactorySatis")
    pruefe(mit._detect_active_world() == "FactorySatis",
           "die Auskunft des Servers schlaegt die Dateizeit")

    ohne = BlueprintManager(blueprint_path=basis)
    pruefe(ohne._detect_active_world() == "BoberKurwa",
           "ohne Auskunft bleibt der alte Weg (juengster Ordner) — kein Rueckschritt")

    aus = BlueprintManager(blueprint_path=basis, session_provider=lambda: None)
    pruefe(aus._detect_active_world() == "BoberKurwa",
           "Server offline: Rueckfall statt leerer Antwort")

    leer = BlueprintManager(blueprint_path=basis, session_provider=lambda: "   ")
    pruefe(leer._detect_active_world() == "BoberKurwa",
           "leerer Sitzungsname zaehlt als keine Auskunft")

    def _kaputt():
        raise RuntimeError("Statusdatei unlesbar")

    kaputt = BlueprintManager(blueprint_path=basis, session_provider=_kaputt)
    pruefe(kaputt._detect_active_world() == "BoberKurwa",
           "ein fehlerhafter Provider reisst die Erkennung nicht mit")

    neu = BlueprintManager(blueprint_path=basis, session_provider=lambda: "FrischeWelt")
    pruefe(neu._detect_active_world() == "FrischeWelt" and (basis / "FrischeWelt").is_dir(),
           "eine noch unbekannte Welt wird uebernommen und ihr Ordner angelegt")

print("\n=== Automatischer Neustart nach Upload ===")
cog = (WURZEL / "cogs" / "satisfactory_cog.py").read_text()
pruefe("async def _neustart_nach_upload" in cog,
       "der Neustart nach Upload ist eine eigene Funktion")
pruefe(cog.count("_neustart_nach_upload(interaction, sid)") == 2,
       "beide Upload-Wege (Sammel-Zip und Einzeldatei) loesen ihn aus")
pruefe("BlueprintRestartView" not in cog,
       "der alte Knopf ist weg — er war der Grund, warum Uploads wirkungslos blieben")
pruefe("asyncio.create_task(self._neustart_nach_upload" in cog,
       "der Countdown laeuft im Hintergrund, die Interaktion haengt nicht fuenf Minuten")
pruefe("`/sat cancel`" in cog,
       "die Antwort nennt den Abbruchweg")

baum = ast.parse(cog)
fn = next(k for k in ast.walk(baum)
          if isinstance(k, ast.AsyncFunctionDef) and k.name == "_neustart_nach_upload")
rumpf = ast.unparse(fn)
pruefe("timer_mgr.has_active" in rumpf,
       "ein bereits laufender Countdown wird nicht ueberfahren")
pruefe("TimerResult.CANCELLED" in rumpf,
       "ein abgebrochener Countdown startet den Server NICHT")
pruefe("har.suppress" in rumpf,
       "die Auto-Restart-Wache wird waehrend des geplanten Neustarts stillgestellt")

print("\n=== Keine fest verdrahteten Instanz-Schluessel mehr ===")
for datei in ("cogs/satisfactory_cog.py", "cogs/scheduler_cog.py",
              "cogs/shutdown_cog.py", "bots/recon_bot.py",
              "modules/monitoring/update_checker.py"):
    inhalt = (WURZEL / datei).read_text()
    pruefe('"sat", "main"' not in inhalt,
           f"{datei} leitet den Schluessel ab statt 'main' zu schreiben")

print()
if fehler:
    print(f"  ERGEBNIS: {len(fehler)} Pruefung(en) fehlgeschlagen.")
    sys.exit(1)
print("  ERGEBNIS: Alle Pruefungen bestanden.")
