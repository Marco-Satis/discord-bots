"""
Disk Guard Module (F49) - Ueberwacht freien Speicherplatz und raeumte automatisch auf.
Background-Task prueft alle 10 Minuten den Speicherplatz und reagiert in drei Stufen:
  Stufe 1 (<20% frei): Admin-Benachrichtigung
  Stufe 2 (<10% frei): Automatisches Loeschen alter Logs/Backups/Temp-Dateien
  Stufe 3 (<5% frei): Kritische Warnung an alle Admins
NIEMALS aktive Savegames oder Config-Dateien loeschen.
"""

import asyncio
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable, Dict, List, Any

import psutil

from utils.logger import get_logger
from utils.config import get_config, get_env, PROJECT_ROOT, LOG_DIR

logger = get_logger("disk_guard")

# Embed-Farben
COLOR_WARNING: int = 0xF39C12
COLOR_ERROR: int = 0xE74C3C
COLOR_SUCCESS: int = 0x2ECC71
COLOR_INFO: int = 0x5865F2

# Geschuetzte Verzeichnis-Muster (werden NIEMALS geloescht)
PROTECTED_PATTERNS: tuple[str, ...] = (
    "SaveGames",
    "savegames",
    "saves",
    "config",
    "Config",
    ".env",
    "config.json",
)


class DiskGuard:
    """
    Ueberwacht den Festplatten-Speicherplatz und fuehrt bei Engpaessen
    automatisch gestufte Aufraeumaktionen durch.

    Usage:
        guard = DiskGuard(bot)
        guard.on_warning = my_warning_callback
        status = await guard.check_disk()
    """

    def __init__(
        self,
        bot: Any,
        *,
        check_interval: int = 600,  # 10 Minuten
        threshold_warn: float = 20.0,    # Stufe 1: <20% frei
        threshold_cleanup: float = 10.0, # Stufe 2: <10% frei
        threshold_critical: float = 5.0, # Stufe 3: <5% frei
        disk_path: str = "/",
    ) -> None:
        self.bot: Any = bot
        self.check_interval: int = check_interval
        self.threshold_warn: float = threshold_warn
        self.threshold_cleanup: float = threshold_cleanup
        self.threshold_critical: float = threshold_critical
        self.disk_path: str = disk_path

        # Verzeichnisse fuer automatische Bereinigung
        self.log_dir: Path = LOG_DIR
        self.backup_dir: Path = PROJECT_ROOT / "backups"
        self.temp_dir: Path = PROJECT_ROOT / "temp"

        # Konfigurierbare Aufbewahrungsfristen (Tage)
        cfg: Dict[str, Any] = get_config().get("disk_guard", {})
        self.log_max_age_days: int = cfg.get("log_max_age_days", 14)
        self.backup_max_age_days: int = cfg.get("backup_max_age_days", 7)

        # Status-Tracking
        self._task: Optional[asyncio.Task[None]] = None
        self._last_alert_time: Dict[str, datetime] = {}
        self._alert_cooldown: int = cfg.get("alert_cooldown_seconds", 1800)  # 30 Min

        # Callbacks
        self.on_warning: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self.on_cleanup: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self.on_critical: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None

        logger.info(
            f"DiskGuard initialisiert: Pfad={disk_path}, "
            f"Schwellen={threshold_warn}/{threshold_cleanup}/{threshold_critical}%"
        )

    # ------------------------------------------------------------------
    # Background-Task Steuerung
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Startet den Background-Check-Task."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="disk_guard_loop")
            logger.info("DiskGuard Background-Task gestartet")

    def stop(self) -> None:
        """Stoppt den Background-Check-Task."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("DiskGuard Background-Task gestoppt")

    async def _loop(self) -> None:
        """Endlos-Schleife: Prueft alle check_interval Sekunden den Speicherplatz."""
        try:
            while True:
                try:
                    result: Dict[str, Any] = await self.check_disk()
                    await self._handle_result(result)
                except asyncio.CancelledError:
                    raise
                except OSError as e:
                    logger.error(f"DiskGuard Pruefung fehlgeschlagen: {e}")
                except RuntimeError as e:
                    logger.error(f"DiskGuard Runtime-Fehler: {e}")

                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            logger.info("DiskGuard Loop beendet")

    # ------------------------------------------------------------------
    # Haupt-Pruefmethode
    # ------------------------------------------------------------------

    async def check_disk(self) -> Dict[str, Any]:
        """
        Prueft den aktuellen Speicherplatz und gibt einen Status-Dict zurueck.

        Returns:
            dict mit Keys:
                - total_gb: Gesamtspeicher in GB
                - used_gb: Belegter Speicher in GB
                - free_gb: Freier Speicher in GB
                - free_percent: Freier Speicher in Prozent
                - used_percent: Belegter Speicher in Prozent
                - level: "ok" | "warning" | "cleanup" | "critical"
                - checked_at: ISO-Zeitstempel
        """
        loop = asyncio.get_running_loop()
        usage = await loop.run_in_executor(None, psutil.disk_usage, self.disk_path)

        free_percent: float = 100.0 - usage.percent

        # Stufe bestimmen
        if free_percent < self.threshold_critical:
            level = "critical"
        elif free_percent < self.threshold_cleanup:
            level = "cleanup"
        elif free_percent < self.threshold_warn:
            level = "warning"
        else:
            level = "ok"

        result: Dict[str, Any] = {
            "total_gb": round(usage.total / (1024 ** 3), 2),
            "used_gb": round(usage.used / (1024 ** 3), 2),
            "free_gb": round(usage.free / (1024 ** 3), 2),
            "free_percent": round(free_percent, 1),
            "used_percent": round(usage.percent, 1),
            "level": level,
            "checked_at": datetime.now().isoformat(),
        }

        logger.debug(
            f"Disk-Check: {result['free_gb']} GB frei "
            f"({result['free_percent']}%), Level={level}"
        )

        return result

    # ------------------------------------------------------------------
    # Reaktion auf Ergebnisse
    # ------------------------------------------------------------------

    async def _handle_result(self, result: Dict[str, Any]) -> None:
        """Reagiert abhaengig von der Stufe auf das Pruefergebnis."""
        level: str = result["level"]

        if level == "ok":
            return

        # Stufe 1: Warnung
        if level == "warning":
            if self._should_alert("warning"):
                logger.warning(
                    f"Speicherplatz-Warnung: Nur noch {result['free_percent']}% frei "
                    f"({result['free_gb']} GB)"
                )
                if self.on_warning:
                    await self.on_warning(result)

        # Stufe 2: Automatisches Aufraeumen
        elif level == "cleanup":
            logger.warning(
                f"Speicherplatz niedrig ({result['free_percent']}% frei) "
                f"- Starte automatische Bereinigung"
            )
            cleanup_result: Dict[str, Any] = await self._auto_cleanup()
            result["cleanup_actions"] = cleanup_result

            if self._should_alert("cleanup"):
                if self.on_cleanup:
                    await self.on_cleanup(result)

        # Stufe 3: Kritische Warnung
        elif level == "critical":
            logger.error(
                f"KRITISCH: Nur noch {result['free_percent']}% Speicherplatz frei! "
                f"({result['free_gb']} GB)"
            )
            # Bei kritischem Level immer zuerst aufraeumen
            cleanup_result = await self._auto_cleanup()
            result["cleanup_actions"] = cleanup_result

            if self._should_alert("critical"):
                if self.on_critical:
                    await self.on_critical(result)

    def _should_alert(self, alert_type: str) -> bool:
        """Prueft ob ein Alert gesendet werden soll (Cooldown-basiert)."""
        now = datetime.now()
        last: Optional[datetime] = self._last_alert_time.get(alert_type)

        if last is None or (now - last).total_seconds() > self._alert_cooldown:
            self._last_alert_time[alert_type] = now
            return True
        return False

    # ------------------------------------------------------------------
    # Automatische Bereinigung
    # ------------------------------------------------------------------

    async def _auto_cleanup(self) -> Dict[str, Any]:
        """
        Fuehrt automatische Bereinigung in definierter Reihenfolge durch:
        1. Alte Logs loeschen
        2. Alte lokale Backups loeschen
        3. Temp-Dateien loeschen
        NIEMALS aktive Savegames oder Config-Dateien.

        Returns:
            dict mit Zusammenfassung der Aktionen
        """
        summary: Dict[str, Any] = {
            "logs_deleted": 0,
            "backups_deleted": 0,
            "temp_deleted": 0,
            "space_freed_mb": 0.0,
        }

        loop = asyncio.get_running_loop()
        space_before: float = psutil.disk_usage(self.disk_path).free

        # Schritt 1: Alte Logs loeschen
        logs_deleted: int = await loop.run_in_executor(
            None, self._delete_old_logs
        )
        summary["logs_deleted"] = logs_deleted

        # Schritt 2: Alte lokale Backups loeschen
        backups_deleted: int = await loop.run_in_executor(
            None, self._delete_old_backups
        )
        summary["backups_deleted"] = backups_deleted

        # Schritt 3: Temp-Dateien loeschen
        temp_deleted: int = await loop.run_in_executor(
            None, self._delete_temp_files
        )
        summary["temp_deleted"] = temp_deleted

        space_after: float = psutil.disk_usage(self.disk_path).free
        summary["space_freed_mb"] = round(
            (space_after - space_before) / (1024 * 1024), 2
        )

        logger.info(
            f"Bereinigung abgeschlossen: {logs_deleted} Logs, "
            f"{backups_deleted} Backups, {temp_deleted} Temp-Dateien geloescht. "
            f"{summary['space_freed_mb']} MB freigegeben"
        )

        return summary

    def _delete_old_logs(self) -> int:
        """Loescht Log-Dateien aelter als log_max_age_days."""
        if not self.log_dir.exists():
            return 0

        cutoff = datetime.now() - timedelta(days=self.log_max_age_days)
        count: int = 0

        # Alle Log-Dateien durchgehen (*.log, *.log.gz, rotierte Logs)
        patterns: list[str] = ["*.log.*", "*.log.gz", "*.gz"]
        for pattern in patterns:
            for log_file in self.log_dir.glob(pattern):
                if self._is_protected(log_file):
                    continue
                try:
                    mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                    if mtime < cutoff:
                        log_file.unlink()
                        count += 1
                        logger.debug(f"Alte Log-Datei geloescht: {log_file.name}")
                except OSError as e:
                    logger.debug(f"Fehler beim Loeschen von {log_file}: {e}")

        return count

    def _delete_old_backups(self) -> int:
        """Loescht lokale Backup-Dateien aelter als backup_max_age_days."""
        if not self.backup_dir.exists():
            return 0

        cutoff = datetime.now() - timedelta(days=self.backup_max_age_days)
        count: int = 0

        patterns: list[str] = ["*.tar.gz", "*.zip", "*.bak"]
        for pattern in patterns:
            for backup_file in self.backup_dir.glob(pattern):
                if self._is_protected(backup_file):
                    continue
                try:
                    mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)
                    if mtime < cutoff:
                        backup_file.unlink()
                        count += 1
                        logger.debug(f"Altes Backup geloescht: {backup_file.name}")
                except OSError as e:
                    logger.debug(f"Fehler beim Loeschen von {backup_file}: {e}")

        return count

    def _delete_temp_files(self) -> int:
        """Loescht alle Dateien im Temp-Verzeichnis."""
        if not self.temp_dir.exists():
            return 0

        count: int = 0
        for temp_file in self.temp_dir.iterdir():
            if temp_file.is_file() and not self._is_protected(temp_file):
                try:
                    temp_file.unlink()
                    count += 1
                    logger.debug(f"Temp-Datei geloescht: {temp_file.name}")
                except OSError as e:
                    logger.debug(f"Fehler beim Loeschen von {temp_file}: {e}")

        return count

    @staticmethod
    def _is_protected(path: Path) -> bool:
        """Prueft ob eine Datei oder ein Pfad geschuetzt ist (Savegames, Config)."""
        path_str: str = str(path)
        for pattern in PROTECTED_PATTERNS:
            if pattern in path_str:
                return True
        return False

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def get_embed_color(self, level: str) -> int:
        """Gibt die passende Embed-Farbe fuer eine Stufe zurueck."""
        colors: Dict[str, int] = {
            "ok": COLOR_SUCCESS,
            "warning": COLOR_WARNING,
            "cleanup": COLOR_WARNING,
            "critical": COLOR_ERROR,
        }
        return colors.get(level, COLOR_INFO)

    def format_status(self, result: Dict[str, Any]) -> str:
        """Formatiert den Disk-Status als lesbaren String."""
        level_labels: Dict[str, str] = {
            "ok": "OK",
            "warning": "Warnung",
            "cleanup": "Bereinigung noetig",
            "critical": "KRITISCH",
        }
        label: str = level_labels.get(result["level"], result["level"])

        lines: list[str] = [
            f"**Festplatten-Status: {label}**",
            f"Gesamt: {result['total_gb']} GB",
            f"Belegt: {result['used_gb']} GB ({result['used_percent']}%)",
            f"Frei: {result['free_gb']} GB ({result['free_percent']}%)",
        ]

        if "cleanup_actions" in result:
            actions: Dict[str, Any] = result["cleanup_actions"]
            lines.append("")
            lines.append("**Bereinigung:**")
            lines.append(f"Logs geloescht: {actions['logs_deleted']}")
            lines.append(f"Backups geloescht: {actions['backups_deleted']}")
            lines.append(f"Temp geloescht: {actions['temp_deleted']}")
            lines.append(f"Freigegeben: {actions['space_freed_mb']} MB")

        return "\n".join(lines)
