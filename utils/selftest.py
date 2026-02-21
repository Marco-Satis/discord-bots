"""
F62: Startup Selftest — Pre-Boot-Pruefung vor bot.run()

Prueft ob alle Voraussetzungen erfuellt sind bevor der Bot startet.
Kritische Fehler verhindern den Start, Warnungen werden geloggt.
"""

import json
import socket
import importlib
from pathlib import Path
from typing import Optional

from utils.config import PROJECT_ROOT, CONFIG_DIR, DATA_DIR, get_env
from utils.logger import get_logger

logger = get_logger("selftest")


class SelftestResult:
    """Ergebnis eines einzelnen Selftest-Checks."""

    def __init__(self, name: str, ok: bool, detail: str,
                 critical: bool = False, category: str = "System"):
        self.name = name
        self.ok = ok
        self.detail = detail
        self.critical = critical  # True = Bot startet NICHT
        self.category = category

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "critical": self.critical,
            "category": self.category,
        }


def run_selftest(bot_name: str, required_env: Optional[list[str]] = None,
                 required_paths: Optional[list[Path]] = None) -> list[SelftestResult]:
    """
    Fuehrt alle Pre-Boot-Checks aus und gibt Ergebnisse zurueck.

    Args:
        bot_name: Name des Bots (gameserver, monitor, admin)
        required_env: Liste kritischer ENV-Variablen
        required_paths: Liste benoetigter Verzeichnisse

    Returns:
        Liste von SelftestResult-Objekten
    """
    results: list[SelftestResult] = []

    # 1. Config-JSON valide
    results.append(_check_config_json())

    # 2. ENV-Variablen vorhanden
    if required_env:
        results.append(_check_env_vars(required_env))

    # 3. Pfade existieren + Schreibrechte
    results.extend(_check_paths(required_paths))

    # 4. Dependencies importierbar
    results.append(_check_dependencies())

    # 5. DNS-Aufloesung
    results.append(_check_dns())

    # 6. Datenverzeichnisse schreibbar
    results.append(_check_data_dirs())

    return results


def _check_config_json() -> SelftestResult:
    """Prueft ob config.json existiert und valides JSON enthaelt."""
    cfg_path = CONFIG_DIR / "config.json"
    try:
        if not cfg_path.exists():
            return SelftestResult(
                "config.json", False, "Datei nicht gefunden",
                critical=False, category="Config"
            )
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return SelftestResult(
                "config.json", False, "Kein gueltiges JSON-Objekt",
                critical=True, category="Config"
            )
        # Pflicht-Keys pruefen
        missing = []
        for key in ("features", "thresholds", "scheduler"):
            if key not in data:
                missing.append(key)
        if missing:
            return SelftestResult(
                "config.json", False,
                f"Fehlende Sektionen: {', '.join(missing)}",
                critical=False, category="Config"
            )
        return SelftestResult(
            "config.json", True, "Valide + alle Pflicht-Keys vorhanden",
            category="Config"
        )
    except json.JSONDecodeError as e:
        return SelftestResult(
            "config.json", False, f"JSON-Fehler: {e}",
            critical=True, category="Config"
        )
    except Exception as e:
        return SelftestResult(
            "config.json", False, f"Lesefehler: {e}",
            critical=True, category="Config"
        )


def _check_env_vars(required: list[str]) -> SelftestResult:
    """Prueft ob alle kritischen ENV-Variablen gesetzt sind."""
    missing = []
    for var in required:
        val = get_env(var)
        if not val:
            missing.append(var)
    if missing:
        return SelftestResult(
            "ENV-Variablen", False,
            f"Fehlend: {', '.join(missing)}",
            critical=True, category="Config"
        )
    return SelftestResult(
        "ENV-Variablen", True,
        f"Alle {len(required)} kritischen Variablen gesetzt",
        category="Config"
    )


def _check_paths(extra_paths: Optional[list[Path]] = None) -> list[SelftestResult]:
    """Prueft ob wichtige Verzeichnisse existieren und schreibbar sind."""
    results = []
    paths_to_check = [
        (CONFIG_DIR, "Config-Verzeichnis", False),
        (DATA_DIR, "Daten-Verzeichnis", True),
        (PROJECT_ROOT / "logs", "Log-Verzeichnis", True),
    ]
    if extra_paths:
        for p in extra_paths:
            paths_to_check.append((p, str(p.name), True))

    for path, name, need_write in paths_to_check:
        try:
            if not path.exists():
                # Versuche zu erstellen wenn Schreibrechte benoetigt
                if need_write:
                    path.mkdir(parents=True, exist_ok=True)
                    results.append(SelftestResult(
                        name, True, f"Erstellt: {path}",
                        category="Pfade"
                    ))
                else:
                    results.append(SelftestResult(
                        name, False, f"Nicht gefunden: {path}",
                        critical=True, category="Pfade"
                    ))
                continue

            if need_write:
                # Schreibtest
                test_file = path / ".selftest_write_check"
                try:
                    test_file.write_text("ok")
                    test_file.unlink()
                    results.append(SelftestResult(
                        name, True, f"OK (schreibbar): {path}",
                        category="Pfade"
                    ))
                except PermissionError:
                    results.append(SelftestResult(
                        name, False, f"Nicht schreibbar: {path}",
                        critical=True, category="Pfade"
                    ))
            else:
                results.append(SelftestResult(
                    name, True, f"OK: {path}",
                    category="Pfade"
                ))
        except Exception as e:
            results.append(SelftestResult(
                name, False, f"Fehler: {e}",
                critical=False, category="Pfade"
            ))

    return results


def _check_dependencies() -> SelftestResult:
    """Prueft ob alle benoetigten Python-Pakete importierbar sind."""
    required_modules = [
        "discord", "dotenv", "aiohttp", "aiofiles", "psutil",
        "fastapi", "uvicorn", "httpx", "jwt", "bcrypt",
        "jinja2", "multipart", "itsdangerous", "aiosqlite",
    ]
    missing = []
    for mod in required_modules:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)

    if missing:
        return SelftestResult(
            "Dependencies", False,
            f"Fehlende Pakete: {', '.join(missing)}",
            critical=len(missing) > 3, category="System"
        )
    return SelftestResult(
        "Dependencies", True,
        f"Alle {len(required_modules)} Pakete verfuegbar",
        category="System"
    )


def _check_dns() -> SelftestResult:
    """Prueft ob DNS-Aufloesung funktioniert."""
    try:
        socket.getaddrinfo("discord.com", 443, socket.AF_INET, socket.SOCK_STREAM)
        return SelftestResult(
            "DNS-Aufloesung", True, "discord.com erreichbar",
            category="Netzwerk"
        )
    except socket.gaierror as e:
        return SelftestResult(
            "DNS-Aufloesung", False, f"DNS fehlgeschlagen: {e}",
            critical=True, category="Netzwerk"
        )
    except Exception as e:
        return SelftestResult(
            "DNS-Aufloesung", False, f"Netzwerk-Fehler: {e}",
            critical=False, category="Netzwerk"
        )


def _check_data_dirs() -> SelftestResult:
    """Prueft und erstellt Bot-Datenverzeichnisse."""
    subdirs = ["gameserver", "monitor", "admin"]
    created = []
    for sub in subdirs:
        d = DATA_DIR / sub
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(sub)

    if created:
        return SelftestResult(
            "Datenverzeichnisse", True,
            f"Erstellt: {', '.join(created)}",
            category="Pfade"
        )
    return SelftestResult(
        "Datenverzeichnisse", True,
        "Alle Unterverzeichnisse vorhanden",
        category="Pfade"
    )


def execute_selftest(bot_name: str,
                     required_env: Optional[list[str]] = None,
                     required_paths: Optional[list[Path]] = None) -> bool:
    """
    Fuehrt Selftest aus und gibt True zurueck wenn der Bot starten darf.

    Loggt alle Ergebnisse. Bei kritischen Fehlern gibt False zurueck.
    """
    logger.info(f"=== Selftest fuer {bot_name} gestartet ===")
    results = run_selftest(bot_name, required_env, required_paths)

    passed = 0
    warnings = 0
    critical_failures = []

    for r in results:
        if r.ok:
            logger.info(f"  [OK] {r.name}: {r.detail}")
            passed += 1
        elif r.critical:
            logger.error(f"  [KRITISCH] {r.name}: {r.detail}")
            critical_failures.append(r)
        else:
            logger.warning(f"  [WARNUNG] {r.name}: {r.detail}")
            warnings += 1

    total = len(results)
    logger.info(f"=== Selftest abgeschlossen: {passed}/{total} OK, "
                f"{warnings} Warnungen, {len(critical_failures)} kritisch ===")

    if critical_failures:
        logger.error(f"Bot {bot_name} kann nicht starten! "
                     f"Kritische Fehler: {[f.name for f in critical_failures]}")
        return False

    return True


def get_selftest_json(bot_name: str,
                      required_env: Optional[list[str]] = None) -> dict:
    """Gibt Selftest-Ergebnis als JSON-Dictionary zurueck (fuer /api/health/selftest)."""
    results = run_selftest(bot_name, required_env)
    return {
        "bot": bot_name,
        "total": len(results),
        "passed": sum(1 for r in results if r.ok),
        "warnings": sum(1 for r in results if not r.ok and not r.critical),
        "critical": sum(1 for r in results if not r.ok and r.critical),
        "checks": [r.to_dict() for r in results],
    }
