#!/usr/bin/env python3
"""
Tick-Rate-Bewertung des Satisfactory-Servers.

Anlass (2026-08-14, Marco): "der laeuft normalerweise mit 60 ticks". Die Ampel
im Status-Panel war auf einen 30er-Sollwert kalibriert (ok ab 25) — ein Server,
der auf die Haelfte einbricht, wurde damit gruen angezeigt. Davor stand sogar
ein erfundener Nenner "/30" in der Anzeige.

Diese Tests halten den Sollwert und die Schwellen fest, damit die Ampel nicht
ein drittes Mal an einem falschen Bezugswert haengt.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.satisfactory.api_client import (  # noqa: E402
    SAT_TICK_SOLL,
    SAT_TICK_WARN,
    SAT_TICK_CRIT,
    tick_zustand,
)

fehler: list[str] = []


def pruefe(bedingung: bool, beschreibung: str) -> None:
    if bedingung:
        print(f"  ok    {beschreibung}")
    else:
        print(f"  FEHLT {beschreibung}")
        fehler.append(beschreibung)


print("\n=== Sollwert ===")
pruefe(SAT_TICK_SOLL == 60.0, "Sollwert ist 60 Ticks/s")
pruefe(SAT_TICK_CRIT < SAT_TICK_WARN < SAT_TICK_SOLL,
       "Schwellen liegen unter dem Soll und in der richtigen Reihenfolge")

print("\n=== Ampel ===")
# Der am 2026-08-14 live gemessene Wert bei leerem Server.
pruefe(tick_zustand(57.2) == "ok", "57,2 Ticks/s (live gemessen) = ok")
pruefe(tick_zustand(60.0) == "ok", "60 Ticks/s = ok")
pruefe(tick_zustand(50.0) == "ok", "genau an der Warn-Schwelle noch ok")
pruefe(tick_zustand(49.9) == "warn", "knapp darunter = warn")

# Das ist der eigentliche Regressionsschutz: mit der alten Kalibrierung
# (ok ab 25) waren beide Werte gruen.
pruefe(tick_zustand(39.0) == "warn", "39 Ticks/s = warn (frueher faelschlich ok)")
pruefe(tick_zustand(30.0) == "warn", "halbe Geschwindigkeit ist nicht ok")
pruefe(tick_zustand(29.9) == "crit", "unter der Haelfte = crit")
pruefe(tick_zustand(0.0) == "crit", "0 Ticks/s = crit")

print("\n=== Anzeige nennt den Bezugswert ===")
recon = (Path(__file__).resolve().parent.parent / "bots" / "recon_bot.py").read_text()
pruefe("SAT_TICK_SOLL" in recon,
       "recon_bot nutzt den gemeinsamen Sollwert statt eigener Zahlen")
pruefe("/30 Ticks" not in recon and "tick_rate >= 25" not in recon,
       "kein erfundener Nenner und keine 25er-Schwelle mehr im Panel")

sat_cog = (Path(__file__).resolve().parent.parent / "cogs" / "satisfactory_cog.py").read_text()
pruefe("SAT_TICK_SOLL" in sat_cog,
       "/sat status zeigt die Tick-Rate mit Bezugswert")

print("\n=== Konfiguration passt zum Soll ===")
cfg = json.loads((Path(__file__).resolve().parent.parent / "config" / "config.json").read_text())
schwelle = cfg["thresholds"]["tick_rate_warning"]
pruefe(schwelle == SAT_TICK_WARN,
       f"config.json tick_rate_warning ({schwelle}) = SAT_TICK_WARN ({SAT_TICK_WARN})")

print()
if fehler:
    print(f"  ERGEBNIS: {len(fehler)} Pruefung(en) fehlgeschlagen.")
    sys.exit(1)
print("  ERGEBNIS: Alle Pruefungen bestanden.")
