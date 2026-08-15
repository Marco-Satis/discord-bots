#!/usr/bin/env python3
"""
Regressions-Test fuer manual_stop_state (Bug vom 2026-08-11).

Symptom: waehrend eines SteamCMD-Updates hat der service_watchdog den
Satisfactory-Server neu gestartet, obwohl update_checker ihn vorher als
manuell-gestoppt markiert hatte.

Ursache: der Watchdog fuehrt seine Units OHNE `.service`-Suffix
("satisfactory"), die Mapping-Tabelle SERVICE_TO_SERVER_ID hat die Schluessel
MIT Suffix. `is_service_manually_stopped("satisfactory")` lief damit ins
Leere und lieferte False.

Dieser Test prueft beide Schreibweisen und faellt ohne den Fix durch.
"""
import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Seit der Registry-Umstellung leitet manual_stop_state seine Zuordnung aus der
# ENV ab. Ohne gesetzte Werte kennt es keinen Minecraft-Server, und der Test
# schlug fehl — nicht wegen eines Fehlers im Code, sondern weil im Dev-Spiegel
# keine config/.env liegt. Ein Test, dessen Ergebnis davon abhaengt, WO er
# laeuft, taugt nicht als Regressionsschutz; deshalb setzt er seine Umgebung
# selbst. setdefault, damit eine echte Konfiguration weiter gewinnt.
os.environ.setdefault("MC_SERVER_IDS", "BMC")
os.environ.setdefault("MC_BMC_SERVICE", "minecraft-bmc.service")
os.environ.setdefault("SAT_SERVER_IDS", "MAIN")
os.environ.setdefault("SATISFACTORY_SERVICE", "satisfactory.service")


def _load_module_direct(name: str, path: Path):
    """Laedt ein Modul direkt aus der Datei.

    Umweg noetig, weil `modules.monitoring.__init__` den halben Bot importiert
    (aiosqlite, discord) — die Dependencies fehlen im Dev-Mirror. Der Test
    braucht nur dieses eine Modul, das ausschliesslich Stdlib nutzt.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mss = _load_module_direct(
    "manual_stop_state", PROJECT_ROOT / "modules/monitoring/manual_stop_state.py"
)

# service_watchdog zieht utils.logger/config nach — fuer den Test genuegen die
# Unit-Namen aus DEFAULT_SERVICES, die per Regex aus der Quelle gelesen werden.
_watchdog_src = (PROJECT_ROOT / "modules/monitoring/service_watchdog.py").read_text(
    encoding="utf-8"
)
_block = re.search(r"DEFAULT_SERVICES[^=]*=\s*\[(.*?)\n\]", _watchdog_src, re.S)
DEFAULT_SERVICES = [
    {"name": n} for n in re.findall(r'"name":\s*"([^"]+)"', _block.group(1) if _block else "")
]

failures: list[str] = []


def check(bedingung: bool, beschreibung: str) -> None:
    if bedingung:
        print(f"  OK    {beschreibung}")
    else:
        print(f"  FEHLT {beschreibung}")
        failures.append(beschreibung)


def main() -> int:
    print("=" * 70)
    print("  manual_stop_state — Service-Namen-Normalisierung")
    print("=" * 70)

    # State in ein Temp-File umlenken, damit der Test den Live-State nicht anfasst
    original_state_file = mss.STATE_FILE
    with tempfile.TemporaryDirectory() as tmp:
        mss.STATE_FILE = Path(tmp) / "manual_stop_state.json"
        try:
            import asyncio

            asyncio.run(mss.mark_stopped("satisfactory"))

            check(
                mss.is_service_manually_stopped("satisfactory.service"),
                "Service-Name MIT Suffix wird erkannt",
            )
            check(
                mss.is_service_manually_stopped("satisfactory"),
                "Service-Name OHNE Suffix wird erkannt (Watchdog-Schreibweise)",
            )
            check(
                mss.server_id_for_service("satisfactory") == "satisfactory",
                "server_id_for_service loest die suffixlose Form auf",
            )
            check(
                mss.server_id_for_service("minecraft-bmc") == "mc_bmc",
                "server_id_for_service loest Minecraft-Units auf",
            )
            check(
                not mss.is_service_manually_stopped("nginx"),
                "Fremder Service blockiert nichts",
            )
            check(
                not mss.is_service_manually_stopped(""),
                "Leerer Service-Name blockiert nichts",
            )

            # Genau die Namen pruefen, mit denen der Watchdog wirklich arbeitet
            for svc in DEFAULT_SERVICES:
                name = svc["name"]
                if mss.server_id_for_service(name) is None:
                    continue  # Unit ohne Manual-Stop-Unterstuetzung, kein Fehler
                asyncio.run(mss.mark_stopped(mss.server_id_for_service(name)))
                check(
                    mss.is_service_manually_stopped(name),
                    f"Watchdog-Name '{name}' trifft die Mapping-Tabelle",
                )
        finally:
            mss.STATE_FILE = original_state_file

    print("-" * 70)
    if failures:
        print(f"  ERGEBNIS: {len(failures)} Pruefung(en) fehlgeschlagen")
        return 1
    print("  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
