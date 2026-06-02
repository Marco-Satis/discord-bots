"""
F42: Automatischer Paket-Update-Check — SQLite-basiert.

Prüft wöchentlich per 'apt list --upgradable' ob System-Updates verfügbar sind.
Security-Updates werden separat erkannt und priorisiert gemeldet.
Ergebnisse werden in SQLite (events-Tabelle) persistiert.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable, Dict, List, Any

from utils.logger import get_logger
from utils.config import get_config, MONITOR_DATA_DIR

logger = get_logger("package_checker")

# Standard: Wöchentlich (7 Tage)
DEFAULT_INTERVAL: int = 604800


class PackageChecker:
    """
    Prüft automatisch auf verfügbare System-Paket-Updates.
    Erkennt Security-Updates anhand der Suite ('security' im Quellnamen).
    Persistiert Ergebnisse in SQLite und schreibt JSON für Dashboard.

    Usage:
        checker = PackageChecker(bot)
        checker.on_updates_available = my_callback
        checker.start()
    """

    def __init__(
        self,
        bot: Any,
        *,
        check_interval: int = DEFAULT_INTERVAL,
    ) -> None:
        self.bot: Any = bot
        self.check_interval: int = check_interval
        self._task: Optional[asyncio.Task[None]] = None
        self._last_result: Optional[Dict[str, Any]] = None

        # Callbacks
        self.on_updates_available: Optional[
            Callable[[Dict[str, Any]], Awaitable[None]]
        ] = None
        self.on_security_updates: Optional[
            Callable[[Dict[str, Any]], Awaitable[None]]
        ] = None

        logger.info(
            f"PackageChecker initialisiert — Intervall: {check_interval}s"
        )

    # ------------------------------------------------------------------
    # Background-Task Steuerung
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Startet den Background-Check-Task."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._loop(), name="package_checker_loop"
            )
            logger.info("PackageChecker Background-Task gestartet")

    def stop(self) -> None:
        """Stoppt den Background-Check-Task."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("PackageChecker Background-Task gestoppt")

    async def _loop(self) -> None:
        """Endlos-Schleife: Prüft im konfigurierten Intervall auf Updates."""
        try:
            while True:
                try:
                    result = await self.check_updates()
                    await self._handle_result(result)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"PackageChecker Fehler: {e}")

                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            logger.info("PackageChecker Loop beendet")

    # ------------------------------------------------------------------
    # Hauptpruefung
    # ------------------------------------------------------------------

    async def check_updates(self) -> Dict[str, Any]:
        """
        Führt 'sudo apt update -qq' und 'apt list --upgradable' aus.

        Returns:
            dict mit Keys:
                - total_updates: Gesamtzahl verfügbarer Updates
                - security_updates: Anzahl Security-Updates
                - packages: Liste der Update-Details
                - checked_at: ISO-Zeitstempel
        """
        result: Dict[str, Any] = {
            "total_updates": 0,
            "security_updates": 0,
            "packages": [],
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
        }

        # Schritt 1: apt update -qq (Paketlisten aktualisieren)
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "apt", "update", "-qq",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=120.0)
        except asyncio.TimeoutError:
            result["error"] = "apt update Timeout (120s)"
            logger.warning(result["error"])
            self._last_result = result
            return result
        except OSError as e:
            result["error"] = f"apt update Fehler: {e}"
            logger.warning(result["error"])
            self._last_result = result
            return result

        # Schritt 2: apt list --upgradable
        try:
            proc = await asyncio.create_subprocess_exec(
                "apt", "list", "--upgradable",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            output = stdout.decode("utf-8", errors="replace")

            packages: List[Dict[str, str]] = []
            security_count = 0

            for line in output.strip().split("\n"):
                if "[upgradable from:" not in line:
                    continue

                parts = line.split("/", 1)
                if len(parts) < 2:
                    continue

                pkg_name = parts[0].strip()
                rest = parts[1]

                # Suite extrahieren (z.B. "jammy-security")
                suite = rest.split(" ")[0] if rest else ""
                is_security = self._is_security(suite)

                # Versionen extrahieren
                version_parts = rest.split(" ")
                new_version = version_parts[1] if len(version_parts) > 1 else "?"
                old_version = "?"
                if "from:" in rest:
                    old_version = rest.split("from:")[-1].strip().rstrip("]").strip()

                pkg_info = {
                    "name": pkg_name,
                    "current": old_version,
                    "available": new_version,
                    "suite": suite,
                    "security": is_security,
                }
                packages.append(pkg_info)
                if is_security:
                    security_count += 1

            result["packages"] = packages
            result["total_updates"] = len(packages)
            result["security_updates"] = security_count

        except asyncio.TimeoutError:
            result["error"] = "apt list Timeout (30s)"
            logger.warning(result["error"])
        except OSError as e:
            result["error"] = f"apt list Fehler: {e}"
            logger.warning(result["error"])

        self._last_result = result
        return result

    @staticmethod
    def _is_security(suite: str) -> bool:
        """Prüft ob eine Suite ein Security-Update enthält."""
        return "security" in suite.lower()

    # ------------------------------------------------------------------
    # Ergebnis-Verarbeitung
    # ------------------------------------------------------------------

    async def _handle_result(self, result: Dict[str, Any]) -> None:
        """Verarbeitet das Prüfungsergebnis: Callbacks und Persistierung."""
        if result.get("error"):
            return

        # JSON fuer Dashboard schreiben
        self._write_status_json(result)

        # In SQLite speichern
        await self._persist_to_db(result)

        # Callbacks
        if result["total_updates"] > 0:
            if self.on_updates_available:
                try:
                    await self.on_updates_available(result)
                except Exception as e:
                    logger.error(f"on_updates_available Callback-Fehler: {e}")

            if result["security_updates"] > 0 and self.on_security_updates:
                try:
                    await self.on_security_updates(result)
                except Exception as e:
                    logger.error(f"on_security_updates Callback-Fehler: {e}")

    def _write_status_json(self, result: Dict[str, Any]) -> None:
        """Schreibt den Status als JSON fuer das Dashboard."""
        from modules.monitoring.status_writer import _write_json_safe
        try:
            MONITOR_DATA_DIR.mkdir(parents=True, exist_ok=True)
            _write_json_safe(MONITOR_DATA_DIR / "package_checker.json", result)
        except Exception as e:
            logger.debug(f"Fehler beim Schreiben des Package-Status: {e}")

    async def _persist_to_db(self, result: Dict[str, Any]) -> None:
        """Speichert das Pruefergebnis als Event in SQLite.

        Nur wenn tatsaechlich Updates gefunden — sonst flutet der taegliche
        Cron den Event-Feed mit '0 Updates'-Eintraegen ohne Informationswert.
        Aktueller Status ist immer in package_checker.json verfuegbar.
        """
        if result.get("total_updates", 0) == 0:
            return
        try:
            from modules.database.db_manager import get_db
            db = await get_db()
            details = json.dumps({
                "total": result["total_updates"],
                "security": result["security_updates"],
                "packages": [p["name"] for p in result.get("packages", [])[:20]],
            })
            await db.execute(
                "INSERT INTO events (timestamp, event_type, category, message, details) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    result["checked_at"],
                    "package_check",
                    "system",
                    f"{result['total_updates']} Updates verfügbar "
                    f"({result['security_updates']} Security)",
                    details,
                ),
            )
            await db.commit()
        except Exception as e:
            logger.debug(f"Package-Check DB-Persistierung fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    @property
    def last_result(self) -> Optional[Dict[str, Any]]:
        """Gibt das letzte Prüfungsergebnis zurück."""
        return self._last_result

    def get_summary(self) -> Dict[str, Any]:
        """Gibt eine Zusammenfassung zurück."""
        if not self._last_result:
            return {"status": "unknown", "message": "Noch kein Check durchgeführt"}
        return {
            "total_updates": self._last_result.get("total_updates", 0),
            "security_updates": self._last_result.get("security_updates", 0),
            "checked_at": self._last_result.get("checked_at"),
        }
