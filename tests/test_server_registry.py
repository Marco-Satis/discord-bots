#!/usr/bin/env python3
"""
Tests für die Server-Registry (utils/config.server_ids).

Welche Spielserver es gibt, stand bis zum 2026-08-14 an drei Stellen im Code —
`bots/recon_bot.py`, `bots/operator_bot.py` und `modules/config_validator.py` —
plus in einem halben Dutzend Anzeige-, Auswahl- und Port-Tabellen. Einen Server
stillzulegen oder zurückzuholen hieß deshalb: Code ändern, testen, deployen.

Jetzt steht es in einer ENV-Variablen. Der wichtigste Test hier ist deshalb
nicht die Zerlegung der Kommaliste, sondern der Nachweis, dass Vanilla
**allein über die Umgebung** zurückkommt — ohne dass eine Code-Zeile anders ist.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_server_registry.py
"""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import server_ids  # noqa: E402 — braucht den sys.path oben

_results: list[tuple[str, bool, str]] = []
WURZEL = Path(__file__).resolve().parent.parent


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


class _Umgebung:
    """ENV-Variablen setzen und danach zuverlässig zurückräumen."""

    def __init__(self, **werte: str) -> None:
        self._werte = werte
        self._vorher: dict[str, str | None] = {}

    def __enter__(self) -> "_Umgebung":
        for k, v in self._werte.items():
            self._vorher[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *_) -> None:
        for k, alt in self._vorher.items():
            if alt is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = alt


def run_tests() -> None:
    # --- 1. Zerlegung ---
    os.environ.pop("MC_SERVER_IDS", None)
    _check("vorgabe_ohne_env", server_ids("MC_SERVER_IDS", "BMC") == ["BMC"])

    with _Umgebung(MC_SERVER_IDS="BMC,VANILLA"):
        _check("kommaliste", server_ids("MC_SERVER_IDS", "BMC") == ["BMC", "VANILLA"])

    with _Umgebung(MC_SERVER_IDS=" bmc , vanilla "):
        _check("leerzeichen_und_kleinschreibung",
               server_ids("MC_SERVER_IDS", "BMC") == ["BMC", "VANILLA"])

    with _Umgebung(MC_SERVER_IDS="BMC,,BMC,VANILLA"):
        _check("dubletten_und_leereintraege",
               server_ids("MC_SERVER_IDS", "BMC") == ["BMC", "VANILLA"])

    with _Umgebung(MC_SERVER_IDS="VANILLA,BMC"):
        _check("reihenfolge_bleibt",
               server_ids("MC_SERVER_IDS", "BMC") == ["VANILLA", "BMC"],
               "die Reihenfolge bestimmt den Vorgabe-Server")

    with _Umgebung(MC_SERVER_IDS=""):
        _check("leere_variable_faellt_auf_vorgabe",
               server_ids("MC_SERVER_IDS", "BMC") == ["BMC"],
               "eine leer gesetzte Variable darf nicht alle Server abschalten")

    # --- 2. Der eigentliche Nachweis: Vanilla kommt allein über die ENV zurück ---
    try:
        from modules.minecraft.server import MinecraftServer

        # Zustand nach der Stilllegung: nur BMC, kein MC_VANILLA_SERVICE
        with _Umgebung(MC_SERVER_IDS="BMC", MC_BMC_SERVICE="minecraft-bmc.service"):
            os.environ.pop("MC_VANILLA_SERVICE", None)
            aktiv = [s for s in server_ids("MC_SERVER_IDS", "BMC")
                     if MinecraftServer(s).enabled]
            _check("stillgelegt_nur_bmc", aktiv == ["BMC"], str(aktiv))

        # Rückweg: Liste erweitern + ENV-Block einkommentieren
        with _Umgebung(MC_SERVER_IDS="BMC,VANILLA",
                       MC_BMC_SERVICE="minecraft-bmc.service",
                       MC_VANILLA_SERVICE="minecraft-vanilla.service"):
            aktiv = [s for s in server_ids("MC_SERVER_IDS", "BMC")
                     if MinecraftServer(s).enabled]
            _check("rueckweg_bringt_vanilla", aktiv == ["BMC", "VANILLA"], str(aktiv))

        # Halber Rückweg: ID gesetzt, aber kein Service -> Server bleibt aus.
        # Das ist der Schutz davor, dass ein Tippfehler eine tote Kachel erzeugt.
        with _Umgebung(MC_SERVER_IDS="BMC,VANILLA",
                       MC_BMC_SERVICE="minecraft-bmc.service"):
            os.environ.pop("MC_VANILLA_SERVICE", None)
            aktiv = [s for s in server_ids("MC_SERVER_IDS", "BMC")
                     if MinecraftServer(s).enabled]
            _check("id_ohne_service_bleibt_aus", aktiv == ["BMC"], str(aktiv))
    except ImportError as e:
        print(f"  [INFO] MinecraftServer nicht importierbar: {str(e)[:70]}")

    # --- 3. Keine dritte Kopie der Liste mehr im Code ---
    kopien: list[str] = []
    for rel in ("bots/recon_bot.py", "bots/operator_bot.py",
                "modules/config_validator.py"):
        text = (WURZEL / rel).read_text(encoding="utf-8")
        if '["BMC", "VANILLA"]' in text or "['BMC', 'VANILLA']" in text:
            kopien.append(rel)
    _check("keine_hartcodierte_serverliste", not kopien,
           "noch aufgezählt in: " + ", ".join(kopien))

    # --- 4. Regressionsschutz: keine Auswahlliste, Anzeigetabelle oder
    #        Port-Zuordnung nennt einen Server, den die Registry nicht kennt ---
    #
    # Das ist der eigentliche Wächter. Ohne ihn schleicht sich beim nächsten
    # Server wieder eine aufgezählte Liste ein, und ein stillgelegter Server
    # bleibt in einem Discord-Menü stehen — genau der Zustand vom 2026-08-14,
    # als Vanilla in vier Auswahllisten und sechs Tabellen hing.
    ZU_PRUEFEN = [
        "cogs/shutdown_cog.py", "cogs/notify_cog.py", "cogs/timeout_cog.py",
        "cogs/monitor_cog.py", "cogs/update_cog.py", "cogs/channel_setup_cog.py",
        "modules/monitoring/manual_stop_state.py",
        "modules/monitoring/status_writer.py",
        "modules/network/port_monitor.py",
        "modules/timeout_manager.py",
        "web/routes/health_route.py", "web/routes/system_route.py",
        "web/routes/server_detail_route.py",
        "web/templates/base.html", "web/templates/base_v5.html",
    ]
    # Server-Literale, die niemand mehr hartcodieren darf. "Vanilla" allein ist
    # bewusst NICHT dabei: in chat_bridge.py und minecraft/server.py meint das
    # Wort die Spielart (Death-Messages, Log-Format), nicht den Server.
    LITERALE = ("mc_vanilla", "MC_VANILLA", '"VANILLA"', "'VANILLA'",
                "Minecraft Vanilla")

    treffer: list[str] = []
    for rel in ZU_PRUEFEN:
        pfad = WURZEL / rel
        if not pfad.exists():
            continue
        quelltext = pfad.read_text(encoding="utf-8")

        if pfad.suffix == ".py":
            # Nur ausführbarer Code zählt. Docstrings und Kommentare dürfen den
            # stillgelegten Server erklären — sonst zwingt der Wächter dazu,
            # die Historie zu verschweigen.
            baum = ast.parse(quelltext)
            docstrings = {
                id(k.body[0].value)
                for k in ast.walk(baum)
                if isinstance(k, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef))
                and k.body and isinstance(k.body[0], ast.Expr)
                and isinstance(k.body[0].value, ast.Constant)
                and isinstance(k.body[0].value.value, str)
            }
            for knoten in ast.walk(baum):
                if (isinstance(knoten, ast.Constant)
                        and isinstance(knoten.value, str)
                        and id(knoten) not in docstrings):
                    for lit in LITERALE:
                        if lit.strip("\"'") in knoten.value:
                            treffer.append(f"{rel}:{knoten.lineno} ({knoten.value[:30]})")
                            break
        else:
            for nr, zeile in enumerate(quelltext.splitlines(), 1):
                if zeile.strip().startswith(("<!--", "#")):
                    continue
                for lit in LITERALE:
                    if lit in zeile:
                        treffer.append(f"{rel}:{nr} ({lit})")
                        break

    _check("keine_hartcodierten_servernamen", not treffer,
           "; ".join(treffer[:6]))


def main() -> int:
    print("=" * 60)
    print("  Server-Registry (utils/config.server_ids)")
    print("=" * 60)

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
