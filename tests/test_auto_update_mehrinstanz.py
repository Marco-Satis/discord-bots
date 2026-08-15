#!/usr/bin/env python3
"""
Auto-Update installiert auf JEDER Satisfactory-Instanz.

Bis 2026-08-15 erkannte der Scheduler Updates zwar je Instanz, installierte
sie aber nur auf der ersten. Die zweite waere auf ihrem Build stehen
geblieben — und ein Satisfactory-Server mit falscher Version nimmt keine
Spieler mehr an. Der Ausfall haette also nicht nach "Update vergessen"
ausgesehen, sondern nach "Server kaputt".

Geprueft wird der Verteiler mit einer Attrappe statt eines echten
SteamCMD-Laufs: welche Instanz wird angefasst, was passiert bei Fehlern, und
bleibt der Zustand je Instanz getrennt.
"""

import ast
import asyncio
import sys
from datetime import datetime, timedelta
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


class ServerAttrappe:
    def __init__(self, name):
        self.display_name = name


class BotAttrappe:
    def __init__(self):
        self.sat_servers = {"MAIN": ServerAttrappe("Satisfactory"),
                            "SECOND": ServerAttrappe("Satisfactory 2")}


def frischer_cog():
    from cogs.scheduler_cog import SchedulerCog
    cog = SchedulerCog.__new__(SchedulerCog)
    cog.bot = BotAttrappe()
    cog._pending_update_je = {}
    cog._last_auto_update_je = {}
    cog._auto_update_fails_je = {}
    cog._auto_update_giveup_je = {}
    cog._pending_update = False
    cog._auto_update_enabled = True
    return cog


print("\n=== Verteiler ===")
cog = frischer_cog()
gerufen: list[str] = []


async def merken(sid, now):
    gerufen.append(sid)


cog._auto_update_einer = merken
jetzt = datetime(2026, 8, 15, 12, 0)

asyncio.run(cog._check_auto_update_install(jetzt))
pruefe(gerufen == [], "ohne ausstehendes Update passiert nichts")

cog._pending_update_je = {"SECOND": True}
asyncio.run(cog._check_auto_update_install(jetzt))
pruefe(gerufen == ["SECOND"],
       "ein Update NUR auf der zweiten Instanz wird auch dort installiert")

gerufen.clear()
cog._pending_update_je = {"MAIN": True, "SECOND": True}
asyncio.run(cog._check_auto_update_install(jetzt))
pruefe(gerufen == ["MAIN", "SECOND"],
       "beide Instanzen nacheinander, nicht gleichzeitig")

print("\n=== Ein Fehler bleibt lokal ===")
gerufen.clear()


async def erste_faellt_um(sid, now):
    if sid == "MAIN":
        raise RuntimeError("SteamCMD abgebrochen")
    gerufen.append(sid)


cog._auto_update_einer = erste_faellt_um
asyncio.run(cog._check_auto_update_install(jetzt))
pruefe(gerufen == ["SECOND"],
       "scheitert die erste Instanz, laeuft die zweite trotzdem")

print("\n=== Zustand je Instanz getrennt ===")
cog = frischer_cog()
cog._pending_setzen("SECOND", True)
pruefe(cog._pending_update_je == {"SECOND": True},
       "_pending_setzen schreibt genau die gemeinte Instanz")
pruefe(cog._pending_update is False,
       "die Anzeige in /scheduler bleibt der Wert der ERSTEN Instanz")
cog._pending_setzen("MAIN", True)
pruefe(cog._pending_update is True,
       "ein Update der ersten Instanz erscheint in der Anzeige")

print("\n=== Cooldown und Loop-Guard je Instanz ===")
quelle = (WURZEL / "cogs" / "scheduler_cog.py").read_text()
baum = ast.parse(quelle)
fn = next(k for k in ast.walk(baum)
          if isinstance(k, ast.AsyncFunctionDef) and k.name == "_auto_update_einer")
rumpf = ast.unparse(fn)
pruefe("_last_auto_update_je" in rumpf,
       "der 30-Minuten-Cooldown zaehlt je Instanz")
pruefe("_auto_update_fails_je" in rumpf and "_auto_update_giveup_je" in rumpf,
       "Fehlversuche und aufgegebener Build werden je Instanz gefuehrt")
pruefe("har.suppress('sat', sid.lower()" in rumpf,
       "die Auto-Restart-Wache wird fuer die richtige Instanz stillgestellt")
pruefe("self.update_checker_von(sid)" in rumpf and "self.sat_server_von(sid)" in rumpf,
       "Update-Checker und Server kommen aus der Instanz-Aufloesung")
pruefe("_perform_rollback(pre_update_backup_path, update_msg, sid)" in rumpf,
       "ein Rollback trifft die Instanz, deren Update scheiterte")
pruefe("{name}" in rumpf,
       "die Meldungen nennen den Instanznamen — sonst nicht zuzuordnen")

print("\n=== Keine Altpfade mehr ===")
pruefe("self._pending_update = True" not in quelle,
       "niemand setzt mehr das alte Sammel-Flag als Ausloeser")
pruefe("_sat_update_pruefen_einer" in quelle,
       "auch die 12:00/00:00-Pruefung laeuft je Instanz")
pruefe("laeuft nur fuer" not in quelle,
       "der Hinweis 'bitte von Hand aktualisieren' ist weg — es geht ja jetzt")

print("\n=== Anzeige zeigt alle Instanzen ===")
pruefe("_offen = [self._sat_name_von(_s)" in quelle,
       "/scheduler und Tagesbericht listen jede Instanz mit offenem Update")

print()
if fehler:
    print(f"  ERGEBNIS: {len(fehler)} Pruefung(en) fehlgeschlagen.")
    sys.exit(1)
print("  ERGEBNIS: Alle Pruefungen bestanden.")
