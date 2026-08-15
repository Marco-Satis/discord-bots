#!/usr/bin/env python3
"""
Der Tick-Alarm muss tatsaechlich feuern.

Anlass (2026-08-14): `tick_rate_warning` stand in der Konfiguration, wurde im
Dashboard angezeigt — und in `PerformanceMonitor.check_thresholds` nie
geprueft. Die Schwelle war eine Behauptung ohne Wirkung.

Die Tests halten drei Dinge fest, die leicht wieder kaputtgehen:
  1. unter der Schwelle wird gewarnt (der eigentliche Alarm)
  2. ohne Messwert (0.0) wird NICHT gewarnt — sonst meldet jeder offline
     stehende Server im Zwei-Minuten-Takt "0 Ticks"
  3. der Cooldown gilt auch fuer die Tick-Warnung
"""

import sys
import asyncio
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.monitoring.performance import (  # noqa: E402
    PerformanceMonitor,
    PerformanceThresholds,
    SystemMetrics,
)
from modules.satisfactory.api_client import SAT_TICK_SOLL  # noqa: E402

fehler: list[str] = []


def pruefe(bedingung: bool, beschreibung: str) -> None:
    if bedingung:
        print(f"  ok    {beschreibung}")
    else:
        print(f"  FEHLT {beschreibung}")
        fehler.append(beschreibung)


def monitor() -> PerformanceMonitor:
    # Systemschwellen hoch setzen: hier soll nur die Tick-Rate entscheiden,
    # sonst faerbt eine ausgelastete Testmaschine das Ergebnis ein.
    return PerformanceMonitor(PerformanceThresholds(
        cpu_warning=999.0, ram_warning=999.0, disk_warning=999.0,
        tick_rate_warning=50.0, warning_cooldown=300,
    ))


print("\n=== Alarm feuert ===")
m = monitor()
w = m.check_thresholds(SystemMetrics(tick_rate=32.0))
pruefe(len(w) == 1, "32 von 60 Ticks loest genau eine Warnung aus")
pruefe(bool(w) and "32.0" in w[0] and f"{SAT_TICK_SOLL:.0f}" in w[0],
       "die Warnung nennt Messwert und Sollwert")

m = monitor()
pruefe(m.check_thresholds(SystemMetrics(tick_rate=49.9)) != [],
       "knapp unter der Schwelle wird gewarnt")

print("\n=== Kein Fehlalarm ===")
m = monitor()
pruefe(m.check_thresholds(SystemMetrics(tick_rate=57.2)) == [],
       "der gesunde Live-Wert 57,2 loest nichts aus")
pruefe(m.check_thresholds(SystemMetrics(tick_rate=50.0)) == [],
       "genau auf der Schwelle noch kein Alarm")

m = monitor()
pruefe(m.check_thresholds(SystemMetrics(tick_rate=0.0)) == [],
       "kein Messwert (0.0) ist KEIN Alarm — dafuer gibt es die Ausfall-Meldung")

print("\n=== Cooldown ===")
m = monitor()
erste = m.check_thresholds(SystemMetrics(tick_rate=20.0))
zweite = m.check_thresholds(SystemMetrics(tick_rate=20.0))
pruefe(erste != [] and zweite == [],
       "zweiter Durchlauf innerhalb des Cooldowns meldet nicht erneut")
# Cooldown kuenstlich ablaufen lassen
m._last_warning_time["tick"] = datetime.now() - timedelta(seconds=400)
pruefe(m.check_thresholds(SystemMetrics(tick_rate=20.0)) != [],
       "nach Ablauf des Cooldowns wird wieder gemeldet")

print("\n=== Messwert kommt bis in die Metrik ===")


async def _collect() -> SystemMetrics:
    return await monitor().collect(server=None, tick_rate=41.5)


metrik = asyncio.run(_collect())
pruefe(metrik.tick_rate == 41.5, "collect() uebernimmt die uebergebene Tick-Rate")

metrik_ohne = asyncio.run(monitor().collect(server=None))
pruefe(metrik_ohne.tick_rate == 0.0,
       "ohne Uebergabe bleibt sie 0.0 (Aufrufer ohne Tick-Wissen loest nichts aus)")

print("\n=== Verdrahtung im Bot ===")
recon = (Path(__file__).resolve().parent.parent / "bots" / "recon_bot.py").read_text()
pruefe("tick_rate=tick_fuer_alarm" in recon,
       "recon_bot gibt die Tick-Rate an den PerformanceMonitor weiter")
pruefe("_tick_alarm_pruefen" in recon,
       "weitere Instanzen haben eine eigene Tick-Pruefung")
pruefe(recon.count("_tick_alarm_pruefen") >= 2,
       "die Pruefung wird auch aufgerufen, nicht nur definiert")

health = (Path(__file__).resolve().parent.parent / "modules" / "monitoring"
          / "health_check.py").read_text()
pruefe("self.status.tick_rate = 0.0" in health,
       "bei totem Prozess wird die Tick-Rate zurueckgesetzt statt stehen zu bleiben")

print()
if fehler:
    print(f"  ERGEBNIS: {len(fehler)} Pruefung(en) fehlgeschlagen.")
    sys.exit(1)
print("  ERGEBNIS: Alle Pruefungen bestanden.")
