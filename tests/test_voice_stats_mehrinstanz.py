#!/usr/bin/env python3
"""
Der Sprachkanal-Zaehler kennt jede Satisfactory-Instanz.

Befund vom 2026-08-15, gefunden von Marco an einem Discord-Screenshot: die
Statuskanaele zeigten SAT-1, MC-BMC und MC-VANILLA — aber kein SAT-2. Der
Mehrinstanz-Umbau hatte Befehle, Panels, Kacheln, Alarme und Backups
umgestellt; `update_voice_stats` in bots/recon_bot.py war uebersehen worden und
fragte weiter die Einzelstuecke `sat_server` und `sat_api` mit fest
geschriebenem Namen "SAT-1".

Das ist genau die Fehlerklasse, um die es im Funktions-Audit geht: der Code war
fehlerfrei und lief taeglich — er deckte nur die Haelfte der Wirklichkeit ab,
und weil die erste Instanz richtig angezeigt wurde, sah alles gesund aus.

Geprueft wird der Quelltext, nicht ein laufender Discord-Client.
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
fn = next(k for k in ast.walk(baum)
          if isinstance(k, ast.AsyncFunctionDef) and k.name == "update_voice_stats")
rumpf = ast.unparse(fn)

print("\n=== Aufloesung ueber die Registry ===")
pruefe("sat_servers.items()" in rumpf,
       "die Schleife laeuft ueber alle Instanzen statt ueber einen Server")
pruefe("sat_apis.get(" in rumpf,
       "die API kommt je Instanz aus sat_apis")
pruefe("sat_health_checkers.get(" in rumpf,
       "der Health-Checker kommt je Instanz")

print("\n=== Keine Einzelstuecke mehr ===")
# Die alten Modulnamen duerfen in DIESER Funktion nicht mehr vorkommen.
# Anderswo im Bot sind sie noch der Migrationsgurt und bleiben erlaubt.
pruefe("await sat_server.is_running()" not in rumpf,
       "kein Zugriff mehr auf den Einzelserver sat_server")
pruefe("sat_api.query_server_state()" not in rumpf,
       "kein Zugriff mehr auf die Einzel-API sat_api")
pruefe("health_checker.status" not in rumpf,
       "kein Zugriff mehr auf den Einzel-Health-Checker")

print("\n=== Kanalname und Schluessel ===")
pruefe('"SAT-1 | ' not in rumpf and "'SAT-1 | " not in rumpf,
       "der Name SAT-1 ist nicht mehr fest geschrieben")
pruefe("_v_label = f'SAT-{_v_index}'" in rumpf or '_v_label = f"SAT-{_v_index}"' in rumpf,
       "der Anzeigename wird aus der Position gebildet")
pruefe("_v_key = _v_label" in rumpf,
       "Schluessel und Anzeigename sind gleich — sonst greift die Teilstring-Suche daneben")

# Der Kanal-Finder sucht ueber 'Schluessel kommt im Namen vor'. Ein Schluessel
# 'SAT' wuerde deshalb auch 'SAT-2 | ...' treffen. Diese Zusicherung haelt fest,
# warum die Schluessel so aussehen muessen, wie sie aussehen.
finder = next(k for k in ast.walk(baum)
              if isinstance(k, ast.AsyncFunctionDef)
              and k.name == "_get_or_create_voice_channel")
pruefe("key.upper() in vc.name.upper()" in ast.unparse(finder),
       "der Finder matcht weiterhin per Teilstring (Grundlage der Regel oben)")
pruefe("SAT-1" not in "SAT-2" and "SAT-2" not in "SAT-1",
       "SAT-1 und SAT-2 sind keine Teilzeichenketten voneinander")

print("\n=== Minecraft bleibt wie es war ===")
pruefe("bot.mc_servers" in rumpf or "mc_servers.items()" in rumpf,
       "die Minecraft-Kanaele laufen weiterhin je Server")
pruefe('f"MC-{sid}' in rumpf or "f'MC-{sid}" in rumpf,
       "der Minecraft-Kanalname kommt aus der Server-Kennung")

print("\n=== Kanaele stillgelegter Server werden entfernt ===")
# Zweiter Teil desselben Befunds: die Schleife legte Kanaele an, entfernte aber
# nie einen. Nach der Stilllegung von Minecraft Vanilla blieb
# 'MC-VANILLA | Offline' stehen und behauptete auf unbestimmte Zeit, es gaebe
# den Server noch.
aufraeumen = next((k for k in ast.walk(baum)
                   if isinstance(k, ast.AsyncFunctionDef)
                   and k.name == "_voice_channels_aufraeumen"), None)
pruefe(aufraeumen is not None, "es gibt eine Aufraeum-Funktion")
pruefe("_voice_channels_aufraeumen(" in rumpf,
       "die Statistik-Schleife ruft sie bei jedem Durchlauf")

if aufraeumen is not None:
    a_rumpf = ast.unparse(aufraeumen)
    pruefe("if not sat_servers_jetzt and (not mc_servers_jetzt)" in a_rumpf
           or "if not sat_servers_jetzt and not mc_servers_jetzt" in a_rumpf,
           "bei komplett leerer Registry wird NICHTS geloescht (Konfigurationsfehler)")
    pruefe("if vc.members" in a_rumpf,
           "ein Kanal mit Zuhoerern bleibt stehen")
    pruefe("vc.delete(" in a_rumpf,
           "geloescht wird ueber die Discord-API")

    # Das Muster aus dem Quelltext holen und wirklich anwenden — nicht eine
    # Kopie davon pruefen, sonst testet man den Test.
    muster_text = None
    for k in ast.walk(aufraeumen):
        if (isinstance(k, ast.Call) and ast.unparse(k.func).endswith("compile")
                and k.args and isinstance(k.args[0], ast.Constant)):
            muster_text = k.args[0].value
    pruefe(muster_text is not None, "das Namensmuster steht als Literal im Code")

    if muster_text:
        import re as _re
        muster = _re.compile(muster_text, _re.IGNORECASE)
        trifft = ["SAT-1 | 🟢 2/4", "SAT-2 | 🔴 Offline",
                  "MC-BMC | 🟢 3/20", "MC-VANILLA | 🔴 Offline"]
        trifft_nicht = ["Allgemein", "Zocken 1", "🎮 Lobby",
                        "SAT Talk", "MC Chat", "satisfactory"]
        pruefe(all(muster.match(n) for n in trifft),
               "das Muster erkennt alle vier Statuskanal-Formen")
        pruefe(not any(muster.match(n) for n in trifft_nicht),
               "es erkennt KEINEN normalen Sprachkanal — sonst loescht der Bot Raeume von Leuten")

print()
if fehler:
    print(f"  ERGEBNIS: {len(fehler)} Pruefung(en) fehlgeschlagen.")
    sys.exit(1)
print("  ERGEBNIS: Alle Pruefungen bestanden.")
