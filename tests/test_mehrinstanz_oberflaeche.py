#!/usr/bin/env python3
"""
Die Bedienoberflaeche kennt jede Satisfactory-Instanz, nicht nur die erste.

Der Registry-Umbau hat Server, APIs, Health-Checker und Scheduler
mehrinstanzfaehig gemacht — Befehle, Panels und Embeds blieben aber auf der
ersten Instanz haengen. Ein zweiter Server waere ueberwacht, gesichert und
alarmiert worden, haette aber in keinem Panel gestanden und auf keinen Befehl
reagiert.

Der Test liest den Quelltext statt einen Discord-Client zu starten: geprueft
wird, dass die Aufloesung ueber die Registry laeuft und keine Einzelstuecke
mehr stehen geblieben sind.
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


def quelle(pfad: str) -> str:
    return (WURZEL / pfad).read_text()


def befehle_mit_server(pfad: str) -> tuple[set, set]:
    """(Befehle mit server-Parameter, Befehle ohne)."""
    baum = ast.parse(quelle(pfad))
    mit, ohne = set(), set()
    for k in ast.walk(baum):
        if not isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any("command(" in ast.unparse(d) for d in k.decorator_list):
            continue
        # Kein and/or-Kurzschluss: eine leere Menge ist falsy, damit landete
        # jeder Treffer faelschlich in "ohne".
        if "server" in {a.arg for a in k.args.args}:
            mit.add(k.name)
        else:
            ohne.add(k.name)
    return mit, ohne


print("\n=== /sat-Befehle ===")
mit, ohne = befehle_mit_server("cogs/satisfactory_cog.py")
pruefe(len(mit) >= 19, f"{len(mit)} von {len(mit) + len(ohne)} Befehlen nehmen einen Server")
# Whitelist/Blacklist sind DB-gestuetzt und spielweit — sie DUERFEN keinen
# Server-Parameter haben, sagen das aber in ihrer Beschreibung.
spielweit = {"whitelist_add", "whitelist_remove", "whitelist_list",
             "blacklist_add", "blacklist_remove", "blacklist_list"}
pruefe(ohne == spielweit,
       "ohne Server-Parameter sind ausschliesslich die spielweiten Listen")
pruefe(quelle("cogs/satisfactory_cog.py").count(
    "gilt fuer alle Satisfactory-Server") == 6,
    "alle sechs sagen in der Beschreibung, dass sie serveruebergreifend gelten")

print("\n=== Bestaetigungs-Views ===")
s = quelle("cogs/satisfactory_cog.py")
# Fuenf Views, seit BlueprintRestartView entfallen ist: der Neustart nach
# einem Upload laeuft jetzt selbsttaetig statt ueber einen Knopf.
_views = s.count("self.srv, self.api, _ = cog._instanz(sid)")
pruefe(_views >= 5, f"jede der {_views} Views loest ihre Zielinstanz auf")
pruefe("self.cog.api" not in s and "self.cog.bot.blueprint_mgr" not in s,
       "keine View greift mehr auf die Einzelstueck-Objekte zu")
pruefe("self._timer_key(sid)" in s,
       "der Neustart-Countdown laeuft unter dem Schluessel der Instanz")
# Nur der AUFRUF zaehlt — in Kommentaren darf cancel_all vorkommen, dort
# erklaert es gerade, warum es nicht mehr benutzt wird.
_aufrufe = {
    ast.unparse(k.func)
    for k in ast.walk(ast.parse(s)) if isinstance(k, ast.Call)
}
pruefe(not any(a.endswith("cancel_all") for a in _aufrufe),
       "sat_cancel raeumt nicht mehr die Countdowns aller Server ab")

print("\n=== uebrige Cogs ===")
mit_u, _ = befehle_mit_server("cogs/update_cog.py")
pruefe({"sat_update_start", "sat_update_cancel"} <= mit_u,
       "/update start und cancel nehmen einen Server")
u = quelle("cogs/update_cog.py")
pruefe('har.unsuppress("sat", "main")' not in u and 'har.suppress("sat", "main"' not in u,
       "die Update-Unterdrueckung nutzt den Schluessel der Instanz, nicht 'main'")

t = quelle("cogs/timeout_cog.py")
pruefe('srv.startswith("sat_")' in t,
       "der Bann-Verteiler erkennt auch die zweite Instanz (Kennung sat_second)")
pruefe("_sat_tracker(sid)" in t or "self._sat_tracker(" in t,
       "Bann und Entbann laufen ueber den Tracker der gemeinten Instanz")

m = quelle("cogs/monitor_cog.py")
mit_m, _ = befehle_mit_server("cogs/monitor_cog.py")
pruefe({"mon_world_cmd", "crashlog_cmd"} <= mit_m,
       "/mon world und /crashlog nehmen einen Server")
pruefe("for _sid, _srv in (self.sat_servers or {}).items():" in m,
       "/performance zeigt die Prozesswerte jeder Instanz")
pruefe("for _bm_sid, sat_bm in sat_bms.items():" in m,
       "die Backup-Uebersicht zeigt jede Instanz")

mo = quelle("cogs/mod_cog.py")
pruefe("def _get_mod_manager(self, game: str, sid" in mo,
       "Mods werden je Instanz aus deren Installationsverzeichnis gelesen")

print("\n=== Panel und Statusdateien ===")
r = quelle("bots/recon_bot.py")
pruefe("for _p_sid, _p_srv in sat_servers.items():" in r,
       "das Statuspanel baut eine Zeile je Instanz")
pruefe('name="Satisfactory", online=False' not in r,
       "die Panel-Zeile nennt den Anzeigenamen der Instanz statt 'Satisfactory'")
pruefe("_weitere_spieler_lesen" in r,
       "weitere Instanzen bekommen einen eigenen Log-Parser fuer Spielernamen")
pruefe("_sat_log_pos" in r and "_sat_log_parser" in r,
       "je Instanz eine eigene Leseposition und ein eigener Parser")

sw = quelle("modules/monitoring/status_writer.py")
pruefe("sat_weitere_online" in sw,
       "der StatusWriter schreibt die Spielerdatei je Instanz")

w = quelle("modules/monitoring/service_watchdog.py")
pruefe("def _standard_dienste()" in w,
       "der Dienst-Watchdog nimmt seine Liste aus der Registry")

print("\n=== Dashboard-Kacheln ===")
d = quelle("web/routes/dashboard.py")
pruefe("from modules.server_registry import alle as _alle_server" in d,
       "die Kachel-Sammlung fragt die Registry")
pruefe("not in _bekannt" in d,
       "Statusdateien ohne Server in der Registry werden uebersprungen")
pruefe("_bekannt is not None" in d,
       "faellt die Registry aus, werden lieber alle Kacheln gezeigt als keine")

if fehler:
    print(f"\n  ERGEBNIS: {len(fehler)} Pruefung(en) fehlgeschlagen.")
    sys.exit(1)
print("\n  ERGEBNIS: Alle Pruefungen bestanden (inkl. Nachtrag).")
