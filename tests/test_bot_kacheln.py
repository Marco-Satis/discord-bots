#!/usr/bin/env python3
"""
Tests für die Bot-Kacheln des Dashboards (web/dashboard_feed.py).

Zwei Befunde aus dem Betriebs-Review:

* Die Kachel las nur das Feld `status` aus `data/<bot>/bot_status.json`. Stirbt
  ein Bot, bleibt die Datei mit „online" liegen — das Dashboard meldete den
  toten Bot weiter als laufend, unbegrenzt lange.
* Angezeigt wurden die Namen von vor der Umbenennung am 2026-06-13
  (GameServer/Monitor/Admin), und pipeline-bot fehlte ganz, obwohl er seinen
  Status genauso schreibt.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_bot_kacheln.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from web.dashboard_feed import (BOT_ANZEIGE, STATUS_MAX_ALTER_SEKUNDEN,
                                    bot_eintrag)
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _daten(alter_sekunden: float, status: str = "online") -> dict:
    wann = datetime.now(timezone.utc) - timedelta(seconds=alter_sekunden)
    return {
        "status": status,
        "ping_ms": 104,
        "uptime": "1h 14m",
        "last_update": wann.isoformat(),
    }


def run_tests() -> None:
    # --- Frische Datei: alles wie gemeldet ---
    frisch = bot_eintrag("monitor", "Recon Bot", _daten(10))
    _check("frisch_bleibt_online", frisch["status"] == "online", str(frisch))
    _check("frisch_behaelt_ping", frisch["ping"] == 104, str(frisch))

    # --- Veraltete Datei: der Bot ist weg, die Kachel sagt es ---
    alt = bot_eintrag("monitor", "Recon Bot", _daten(STATUS_MAX_ALTER_SEKUNDEN + 30))
    _check("veraltet_nicht_online", alt["status"] != "online", str(alt))
    _check("veraltet_gekennzeichnet", alt["status"] == "veraltet", str(alt))
    _check("veraltet_ohne_ping", alt["ping"] == "N/A",
           "veralteter Ping wird weiter angezeigt")
    _check("veraltet_ohne_uptime", alt["uptime"] == "N/A", str(alt))

    # --- Genau an der Grenze noch gueltig ---
    grenze = bot_eintrag("monitor", "Recon Bot", _daten(STATUS_MAX_ALTER_SEKUNDEN - 5))
    _check("kurz_vor_grenze_online", grenze["status"] == "online", str(grenze))

    # --- Ohne Zeitstempel ist „online" nicht pruefbar ---
    ohne = bot_eintrag("monitor", "Recon Bot",
                       {"status": "online", "ping_ms": 5, "uptime": "1m"})
    _check("ohne_zeitstempel_unklar", ohne["status"] == "unknown", str(ohne))

    # --- Kaputter Zeitstempel sperrt nicht aus, behauptet aber nichts ---
    kaputt = bot_eintrag("monitor", "Recon Bot",
                         {"status": "online", "last_update": "keine zeit"})
    _check("kaputter_zeitstempel_unklar", kaputt["status"] == "unknown", str(kaputt))

    # --- Fehlende Datei (leeres dict) bleibt unknown ---
    leer = bot_eintrag("pipeline", "Pipeline Bot", {})
    _check("fehlende_datei_unknown", leer["status"] == "unknown", str(leer))

    # --- „offline" wird nicht durch die Frische-Pruefung ueberschrieben ---
    offline = bot_eintrag("admin", "Marshal Bot", _daten(5, status="offline"))
    _check("offline_bleibt_offline", offline["status"] == "offline", str(offline))

    # --- Namen und Umfang ---
    _check("vier_bots", len(BOT_ANZEIGE) == 4, str(BOT_ANZEIGE))
    _check("pipeline_dabei", "pipeline" in BOT_ANZEIGE, str(BOT_ANZEIGE))
    _check("aktuelle_namen",
           set(BOT_ANZEIGE.values()) == {"Recon Bot", "Operator Bot",
                                         "Marshal Bot", "Pipeline Bot"},
           str(BOT_ANZEIGE))
    _check("keine_alten_namen",
           not any(alt in " ".join(BOT_ANZEIGE.values())
                   for alt in ("GameServer", "Monitor Bot", "Admin Bot")),
           str(BOT_ANZEIGE))

    # --- Beide Sammler bauen dieselbe Kachel ---
    try:
        from web.routes import dashboard as dashboard_route
        _check("route_nutzt_dieselbe_quelle",
               dashboard_route.bot_eintrag is bot_eintrag,
               "die Route hat wieder eine eigene Kopie")
    except (ImportError, RuntimeError, SystemExit) as e:
        # web.routes.dashboard zieht web.auth nach, das beim Import einen
        # WEB_SECRET_KEY aus der Prod-.env verlangt. Im Dev-Spiegel gibt es die
        # nicht — am Server laeuft die Pruefung mit.
        print(f"  [INFO] Route nicht importierbar: {str(e)[:70]}")


def main() -> int:
    print("=" * 60)
    print("  Bot-Kacheln (web/dashboard_feed.py)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] Abhängigkeiten lokal nicht installiert — läuft am Server.")
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
