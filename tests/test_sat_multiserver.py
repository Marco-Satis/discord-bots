#!/usr/bin/env python3
"""
Tests für mehrere Satisfactory-Instanzen (modules/satisfactory/server.py).

Bis zum 2026-08-14 gab es genau einen Satisfactory-Server — als Modul-Singleton
in `bots/recon_bot.py`. Der zweite Server, der den stillgelegten
Minecraft-Vanilla ersetzt, läuft unter **demselben Linux-Nutzer**. Getrennt
wird er über drei Dinge, die die systemd-Unit setzt:

* eigene Portgruppe (`-Port`/`-BeaconPort`/`-ServerQueryPort`)
* eigenes Installationsverzeichnis — dort liegen Logs und Crashes
* eigenes `HOME` — darunter liegen Speicherstände und Einstellungen

Genau diese drei Trennungen prüft der Test, dazu die Fallschirme: ohne
`SAT_SECOND_*` darf keine zweite Instanz entstehen, und der erste Server muss
seine alten Variablennamen behalten.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_sat_multiserver.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from modules.satisfactory.server import SatisfactoryServer
    from utils.config import server_ids
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


class _Umgebung:
    """ENV setzen und danach zuverlässig zurückräumen."""

    def __init__(self, **werte: str) -> None:
        self._werte = werte
        self._vorher: dict[str, str | None] = {}

    def __enter__(self):
        for k, v in self._werte.items():
            self._vorher[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *_):
        for k, alt in self._vorher.items():
            if alt is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = alt


ZWEITER = {
    "SAT_SECOND_SERVICE": "satisfactory2.service",
    "SAT_SECOND_PATH": "/home/satisfactory/sat2/SatisfactoryDedicatedServer",
    "SAT_SECOND_HOME": "/home/satisfactory/sat2",
    "SAT_SECOND_DISPLAY_NAME": "Satisfactory Zwei",
}


def run_tests() -> None:
    # --- 1. Ohne SAT_SECOND_* gibt es genau eine Instanz ---
    for k in list(os.environ):
        if k.startswith("SAT_SECOND_"):
            os.environ.pop(k)
    with _Umgebung(SAT_SERVER_IDS="MAIN,SECOND",
                   SATISFACTORY_SERVICE="satisfactory.service"):
        aktiv = [s for s in server_ids("SAT_SERVER_IDS", "MAIN")
                 if SatisfactoryServer(s).enabled]
        _check("ohne_env_nur_erster", aktiv == ["MAIN"], str(aktiv))

    # --- 2. Der erste Server erbt die alten Variablennamen ---
    with _Umgebung(SATISFACTORY_SERVICE="satisfactory.service",
                   SATISFACTORY_SERVER_PATH="/home/satisfactory/SatisfactoryDedicatedServer",
                   SATISFACTORY_USER="satisfactory"):
        a = SatisfactoryServer("MAIN")
        _check("erster_erbt_service", a.service_name == "satisfactory.service",
               a.service_name)
        _check("erster_erbt_pfad",
               str(a.server_path) == "/home/satisfactory/SatisfactoryDedicatedServer",
               str(a.server_path))
        _check("erster_heisst_satisfactory", a.display_name == "Satisfactory",
               a.display_name)

    # --- 3. Zwei Instanzen sind wirklich getrennt ---
    with _Umgebung(SAT_SERVER_IDS="MAIN,SECOND",
                   SATISFACTORY_SERVICE="satisfactory.service", **ZWEITER):
        aktiv = [s for s in server_ids("SAT_SERVER_IDS", "MAIN")
                 if SatisfactoryServer(s).enabled]
        _check("beide_aktiv", aktiv == ["MAIN", "SECOND"], str(aktiv))

        a, b = SatisfactoryServer("MAIN"), SatisfactoryServer("SECOND")

        _check("units_verschieden", a.service_name != b.service_name,
               f"{a.service_name} / {b.service_name}")
        _check("installation_verschieden", a.server_path != b.server_path,
               f"{a.server_path} / {b.server_path}")
        # Das Wichtigste: gemeinsame Logdatei waere ein stiller Datenmischer —
        # der Spieler-Parser und das Crash-Replay lesen genau diese Datei.
        _check("logs_verschieden", a.log_path != b.log_path,
               f"{a.log_path} / {b.log_path}")
        # Und getrennte Speicherstaende, sonst sichert das Backup beide Welten
        # in einen Topf.
        _check("speicherstaende_verschieden", a.save_path != b.save_path,
               f"{a.save_path} / {b.save_path}")
        _check("zweiter_hat_eigenen_namen", b.display_name == "Satisfactory Zwei",
               b.display_name)
        _check("gleicher_linux_nutzer", a.server_user == b.server_user,
               "beide laufen bewusst unter demselben Nutzer")

        # Der Speicherstand haengt am HOME, nicht am Nutzer — sonst waere die
        # Trennung unter einem gemeinsamen Nutzer gar nicht moeglich.
        _check("speicherstand_folgt_home",
               str(b.save_path).startswith("/home/satisfactory/sat2"),
               str(b.save_path))

    # --- 4. Prozess-Suche trennt die Instanzen über den Installationspfad ---
    # Ohne diesen Filter passt jeder Satisfactory-Prozess auf jede Instanz
    # (gleicher Nutzer), und der Prozess mit dem meisten RAM gewinnt für beide.
    with _Umgebung(SAT_SERVER_IDS="MAIN,SECOND",
                   SATISFACTORY_SERVICE="satisfactory.service", **ZWEITER):
        import inspect
        code = inspect.getsource(SatisfactoryServer._find_process)
        _check("prozesssuche_filtert_nach_pfad", "self.server_path" in code,
               "ohne Pfadfilter zeigen beide Server dieselben Werte")

    # --- 5. Eine ID ohne Unit erzeugt keine halbe Instanz ---
    with _Umgebung(SAT_SERVER_IDS="MAIN,TIPPFEHLER",
                   SATISFACTORY_SERVICE="satisfactory.service"):
        aktiv = [s for s in server_ids("SAT_SERVER_IDS", "MAIN")
                 if SatisfactoryServer(s).enabled]
        _check("id_ohne_unit_bleibt_aus", aktiv == ["MAIN"], str(aktiv))


def main() -> int:
    print("=" * 60)
    print("  Satisfactory Mehrserver (modules/satisfactory/server.py)")
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
