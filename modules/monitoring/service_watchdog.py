"""
Service Watchdog Module (F50) - Überwacht systemd-Services und startet sie bei Ausfall neu.
Prüft alle 2 Minuten per systemctl is-active ob konfigurierte Services laufen.
Bei Ausfall: automatischer Neustart mit Cooldown (max 3 Restarts pro Stunde pro Service).
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable, Dict, List, Any
from collections import defaultdict

from utils.logger import get_logger
from utils.config import get_config, get_env

logger = get_logger("service_watchdog")

# Embed-Farben
COLOR_WARNING: int = 0xF39C12
COLOR_ERROR: int = 0xE74C3C
COLOR_SUCCESS: int = 0x2ECC71
COLOR_INFO: int = 0x5865F2

# Standardmäßig überwachte Services (kann per Config überschrieben werden)
DEFAULT_SERVICES: list[Dict[str, str]] = [
    {"name": "satisfactory", "label": "Satisfactory Server"},
]


class ServiceWatchdog:
    """
    Überwacht systemd-Services und startet sie bei Ausfall automatisch neu.
    Implementiert einen Cooldown, um Restart-Schleifen zu verhindern.

    Usage:
        watchdog = ServiceWatchdog(bot)
        watchdog.on_service_down = my_down_callback
        watchdog.on_restart_success = my_restart_callback
        results = await watchdog.check_services()
    """

    def __init__(
        self,
        bot: Any,
        *,
        check_interval: int = 120,  # 2 Minuten
        max_restarts_per_hour: int = 3,
        services: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        self.bot: Any = bot
        self.check_interval: int = check_interval
        self.max_restarts_per_hour: int = max_restarts_per_hour

        # Services aus Config oder Parameter laden
        cfg: Dict[str, Any] = get_config().get("service_watchdog", {})
        if services is not None:
            self.services: List[Dict[str, str]] = services
        else:
            self.services = cfg.get("services", DEFAULT_SERVICES)

        # Restart-Historie pro Service: {service_name: [datetime, ...]}
        self._restart_history: Dict[str, List[datetime]] = defaultdict(list)

        # Letzter bekannter Status pro Service
        self._last_status: Dict[str, str] = {}

        # Task-Referenz
        self._task: Optional[asyncio.Task[None]] = None

        # Callbacks
        self.on_service_down: Optional[
            Callable[[str, str], Awaitable[None]]
        ] = None  # (service_name, label)
        self.on_restart_success: Optional[
            Callable[[str, str], Awaitable[None]]
        ] = None  # (service_name, label)
        self.on_restart_failed: Optional[
            Callable[[str, str, str], Awaitable[None]]
        ] = None  # (service_name, label, error_msg)
        self.on_cooldown_reached: Optional[
            Callable[[str, str, int], Awaitable[None]]
        ] = None  # (service_name, label, restart_count)

        logger.info(
            f"ServiceWatchdog initialisiert: {len(self.services)} Services, "
            f"Intervall={check_interval}s, Max-Restarts={max_restarts_per_hour}/h"
        )

    # ------------------------------------------------------------------
    # Background-Task Steuerung
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Startet den Background-Monitoring-Task."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._loop(), name="service_watchdog_loop"
            )
            logger.info("ServiceWatchdog Background-Task gestartet")

    def stop(self) -> None:
        """Stoppt den Background-Monitoring-Task."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("ServiceWatchdog Background-Task gestoppt")

    async def _loop(self) -> None:
        """Endlos-Schleife: Prüft alle check_interval Sekunden die Services."""
        try:
            while True:
                try:
                    results: List[Dict[str, Any]] = await self.check_services()
                    await self._handle_results(results)
                except asyncio.CancelledError:
                    raise
                except OSError as e:
                    logger.error(f"ServiceWatchdog Prüfung fehlgeschlagen: {e}")
                except RuntimeError as e:
                    logger.error(f"ServiceWatchdog Runtime-Fehler: {e}")

                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            logger.info("ServiceWatchdog Loop beendet")

    # ------------------------------------------------------------------
    # Haupt-Pruefmethode
    # ------------------------------------------------------------------

    async def check_services(self) -> List[Dict[str, Any]]:
        """
        Prüft alle konfigurierten Services per systemctl is-active.

        Returns:
            Liste von Dicts mit Keys:
                - name: Service-Name (systemd Unit)
                - label: Menschenlesbarer Name
                - active: True wenn der Service läuft
                - status: Status-String von systemctl
                - checked_at: ISO-Zeitstempel
        """
        results: List[Dict[str, Any]] = []

        for service in self.services:
            name: str = service["name"]
            label: str = service.get("label", name)

            status: str = await self._get_service_status(name)
            is_active: bool = status == "active"

            result: Dict[str, Any] = {
                "name": name,
                "label": label,
                "active": is_active,
                "status": status,
                "checked_at": datetime.now().isoformat(),
            }
            results.append(result)

            logger.debug(f"Service '{name}' ({label}): {status}")

        return results

    async def _get_service_status(self, service_name: str) -> str:
        """
        Fragt den Status eines systemd-Service per systemctl is-active ab.

        Returns:
            Status-String: "active", "inactive", "failed", "activating" etc.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return stdout.decode("utf-8", errors="ignore").strip()
        except asyncio.TimeoutError:
            logger.warning(f"Timeout bei Status-Abfrage fuer '{service_name}'")
            return "timeout"
        except OSError as e:
            logger.error(f"Fehler bei systemctl fuer '{service_name}': {e}")
            return "error"

    # ------------------------------------------------------------------
    # Reaktion auf Ergebnisse
    # ------------------------------------------------------------------

    async def _handle_results(self, results: List[Dict[str, Any]]) -> None:
        """Verarbeitet die Prüfergebnisse und startet ggf. Services neu."""
        for result in results:
            name: str = result["name"]
            label: str = result["label"]
            is_active: bool = result["active"]
            prev_status: Optional[str] = self._last_status.get(name)

            self._last_status[name] = result["status"]

            if is_active:
                # Service läuft, alles gut
                continue

            # Service ist nicht aktiv
            logger.warning(
                f"Service '{name}' ({label}) ist nicht aktiv: {result['status']}"
            )

            # Callback: Service down
            if self.on_service_down:
                try:
                    await self.on_service_down(name, label)
                except Exception as e:
                    logger.error(f"on_service_down Callback-Fehler: {e}")

            # Prüfen ob Restart erlaubt ist (Cooldown)
            if not self._can_restart(name):
                restart_count: int = self._restarts_last_hour(name)
                logger.warning(
                    f"Cooldown aktiv für '{name}': {restart_count} Restarts "
                    f"in der letzten Stunde (Max: {self.max_restarts_per_hour})"
                )
                if self.on_cooldown_reached:
                    try:
                        await self.on_cooldown_reached(name, label, restart_count)
                    except Exception as e:
                        logger.error(f"on_cooldown_reached Callback-Fehler: {e}")
                continue

            # Neustart versuchen
            success, error_msg = await self._restart_service(name)

            if success:
                logger.info(f"Service '{name}' ({label}) erfolgreich neu gestartet")
                if self.on_restart_success:
                    try:
                        await self.on_restart_success(name, label)
                    except Exception as e:
                        logger.error(f"on_restart_success Callback-Fehler: {e}")
            else:
                logger.error(
                    f"Neustart von '{name}' ({label}) fehlgeschlagen: {error_msg}"
                )
                if self.on_restart_failed:
                    try:
                        await self.on_restart_failed(name, label, error_msg)
                    except Exception as e:
                        logger.error(f"on_restart_failed Callback-Fehler: {e}")

    # ------------------------------------------------------------------
    # Service-Neustart
    # ------------------------------------------------------------------

    async def _restart_service(self, service_name: str) -> tuple[bool, str]:
        """
        Startet einen systemd-Service per sudo systemctl restart neu.

        Returns:
            Tuple (success, error_message)
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "restart", service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            # Restart in Historie aufnehmen
            self._restart_history[service_name].append(datetime.now())

            if proc.returncode == 0:
                # Kurz warten und prüfen ob Service wirklich läuft
                await asyncio.sleep(3)
                status: str = await self._get_service_status(service_name)
                if status == "active":
                    return True, ""
                return False, f"Service nach Restart im Status: {status}"

            error_text: str = stderr.decode("utf-8", errors="ignore").strip()
            return False, f"systemctl restart Fehler (Code {proc.returncode}): {error_text}"

        except asyncio.TimeoutError:
            return False, "Timeout beim Service-Neustart (>30s)"
        except OSError as e:
            return False, f"OS-Fehler: {e}"

    # ------------------------------------------------------------------
    # Cooldown-Verwaltung
    # ------------------------------------------------------------------

    def _can_restart(self, service_name: str) -> bool:
        """Prüft ob ein Restart für den Service erlaubt ist (Cooldown)."""
        return self._restarts_last_hour(service_name) < self.max_restarts_per_hour

    def _restarts_last_hour(self, service_name: str) -> int:
        """Zählt die Restarts eines Services in der letzten Stunde."""
        cutoff: datetime = datetime.now() - timedelta(hours=1)
        history: List[datetime] = self._restart_history.get(service_name, [])

        # Alte Einträge bereinigen
        recent: List[datetime] = [ts for ts in history if ts > cutoff]
        self._restart_history[service_name] = recent

        return len(recent)

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def get_embed_color(self, active: bool) -> int:
        """Gibt die passende Embed-Farbe zurück."""
        return COLOR_SUCCESS if active else COLOR_ERROR

    def get_summary(self) -> Dict[str, Any]:
        """Gibt eine Zusammenfassung aller Service-Status zurück."""
        return {
            "services": [
                {
                    "name": svc["name"],
                    "label": svc.get("label", svc["name"]),
                    "last_status": self._last_status.get(svc["name"], "unbekannt"),
                    "restarts_last_hour": self._restarts_last_hour(svc["name"]),
                }
                for svc in self.services
            ],
            "total_services": len(self.services),
        }

    def format_status(self, results: List[Dict[str, Any]]) -> str:
        """Formatiert die Service-Status als lesbaren String."""
        lines: list[str] = ["**Service-Status:**", ""]

        for result in results:
            icon: str = "+" if result["active"] else "-"
            restart_count: int = self._restarts_last_hour(result["name"])
            restart_info: str = (
                f" (Restarts: {restart_count})" if restart_count > 0 else ""
            )
            lines.append(
                f"[{icon}] {result['label']}: {result['status']}{restart_info}"
            )

        return "\n".join(lines)
