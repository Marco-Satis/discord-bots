"""
F36: OneDrive Backup Status — Parst rclone-Logs und sammelt Cloud-Backup-Status.

Liest Log-Dateien aus dem logs/-Verzeichnis, extrahiert erfolgreiche
OneDrive-Uploads und fragt optional den Cloud-Speicherverbrauch
via 'rclone about' ab. Dient als Datenquelle fuer das Dashboard
und die Offsite-Backup-Uebersicht.
"""

import re
import json
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

from utils.config import PROJECT_ROOT
from utils.logger import get_logger

logger = get_logger("backup.onedrive_status")

# Log-Verzeichnis (identisch mit utils.config.LOG_DIR)
LOG_DIR: Path = PROJECT_ROOT / "logs"

# Pattern fuer erfolgreiche Upload-Zeilen im Log:
#   "2026-02-22 00:48:25 [onedrive_backup] INFO: Upload successful: dateiname.tar.gz (5.1 MB)"
_UPLOAD_SUCCESS_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+"      # Zeitstempel
    r"\[onedrive_backup\]\s+INFO:\s+"                      # Logger-Name + Level
    r"Upload successful:\s+"                                # Marker
    r"(.+?)\s+"                                             # Dateiname (non-greedy)
    r"\(([^)]+)\)$"                                         # Groesse in Klammern
)

# Maximale Zeilen pro Log-Datei die gescannt werden
_MAX_LINES_PER_FILE: int = 10000

# Timeout fuer rclone-Befehle (Sekunden)
_RCLONE_TIMEOUT: float = 30.0


class OnedriveStatus:
    """
    Sammelt und aggregiert den Status der OneDrive-Cloud-Backups.

    Parst rclone-Logs nach erfolgreichen Uploads und fragt
    optional den Cloud-Speicherverbrauch via 'rclone about' ab.

    Usage:
        status = OnedriveStatus()
        summary = await status.get_status_summary()
    """

    def __init__(
        self,
        log_dir: Optional[Path] = None,
        remote_name: str = "onedrive",
    ) -> None:
        """
        Args:
            log_dir: Verzeichnis mit Log-Dateien (Standard: PROJECT_ROOT / "logs")
            remote_name: Name des rclone-Remotes (Standard: "onedrive")
        """
        self._log_dir = log_dir or LOG_DIR
        self._remote_name = remote_name

        logger.info(
            "OnedriveStatus initialisiert (Logs: %s, Remote: %s:)",
            self._log_dir, self._remote_name,
        )

    # ------------------------------------------------------------------
    #  Log-Parsing
    # ------------------------------------------------------------------

    def _parse_log_file(self, log_path: Path) -> list[dict]:
        """
        Scannt eine einzelne Log-Datei nach erfolgreichen Upload-Zeilen.

        Args:
            log_path: Pfad zur .log-Datei

        Returns:
            Liste von Dicts mit Keys: timestamp, filename, size
        """
        entries: list[dict] = []

        try:
            if not log_path.exists() or log_path.stat().st_size == 0:
                return entries

            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                # Nur die letzten N Zeilen scannen (Performance)
                recent_lines = lines[-_MAX_LINES_PER_FILE:]

            for line in recent_lines:
                line = line.strip()
                match = _UPLOAD_SUCCESS_PATTERN.match(line)
                if match:
                    timestamp_str, filename, size_str = match.groups()
                    entries.append({
                        "timestamp": timestamp_str,
                        "filename": filename.strip(),
                        "size": size_str.strip(),
                    })

        except (IOError, UnicodeDecodeError, OSError) as e:
            logger.warning("Fehler beim Lesen von %s: %s", log_path, e)

        return entries

    def _collect_all_uploads(self) -> list[dict]:
        """
        Sammelt alle erfolgreichen Upload-Eintraege aus allen Log-Dateien.
        Sortiert nach Zeitstempel (neueste zuerst).

        Returns:
            Liste aller Upload-Eintraege
        """
        all_entries: list[dict] = []

        if not self._log_dir.exists():
            logger.debug("Log-Verzeichnis nicht gefunden: %s", self._log_dir)
            return all_entries

        # Alle .log-Dateien durchsuchen (inkl. rotierte wie onedrive_backup.log.1)
        for log_file in self._log_dir.iterdir():
            if not log_file.is_file():
                continue
            # Nur Dateien die mit .log enden oder onedrive_backup im Namen haben
            name = log_file.name.lower()
            if name.endswith(".log") or "onedrive_backup" in name:
                entries = self._parse_log_file(log_file)
                all_entries.extend(entries)

        # Nach Zeitstempel sortieren (neueste zuerst)
        all_entries.sort(key=lambda e: e["timestamp"], reverse=True)

        return all_entries

    # ------------------------------------------------------------------
    #  Oeffentliche Methoden
    # ------------------------------------------------------------------

    def get_recent_uploads(self, limit: int = 10) -> list[dict]:
        """
        Gibt die letzten erfolgreichen Uploads zurueck.

        Args:
            limit: Maximale Anzahl der Ergebnisse (Standard: 10)

        Returns:
            Liste von Dicts: [{"timestamp": str, "filename": str, "size": str}, ...]
        """
        try:
            all_uploads = self._collect_all_uploads()
            return all_uploads[:limit]
        except Exception as e:
            logger.error("Fehler beim Sammeln der Upload-Historie: %s", e)
            return []

    def get_last_successful_upload(self) -> Optional[dict]:
        """
        Gibt den letzten erfolgreichen Upload zurueck.

        Returns:
            Dict mit Keys timestamp, filename, size — oder None falls kein Upload gefunden
        """
        try:
            uploads = self.get_recent_uploads(limit=1)
            return uploads[0] if uploads else None
        except Exception as e:
            logger.error("Fehler beim Ermitteln des letzten Uploads: %s", e)
            return None

    async def get_cloud_storage_usage(self) -> Optional[dict]:
        """
        Fragt den Cloud-Speicherverbrauch via 'rclone about' ab.

        Fuehrt 'rclone about onedrive: --json' aus und parst das JSON-Ergebnis.

        Returns:
            Dict mit Keys: total, used, free, trashed (jeweils in Bytes)
            oder None bei Fehler (rclone nicht installiert, Remote nicht konfiguriert etc.)
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "rclone", "about", f"{self._remote_name}:", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_RCLONE_TIMEOUT
            )

            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.warning(
                    "rclone about fehlgeschlagen (Code %d): %s",
                    proc.returncode, error_msg[:200],
                )
                return None

            data = json.loads(stdout.decode("utf-8", errors="replace"))

            # rclone gibt total, used, free, trashed als Integer (Bytes) zurueck
            result = {
                "total": data.get("total", 0),
                "used": data.get("used", 0),
                "free": data.get("free", 0),
                "trashed": data.get("trashed", 0),
            }

            logger.debug(
                "Cloud-Speicher: %.1f GB belegt / %.1f GB gesamt",
                result["used"] / (1024 ** 3),
                result["total"] / (1024 ** 3),
            )
            return result

        except FileNotFoundError:
            logger.warning("rclone nicht installiert — Cloud-Speicher nicht abfragbar")
            return None
        except asyncio.TimeoutError:
            logger.warning("rclone about Timeout nach %.0fs", _RCLONE_TIMEOUT)
            return None
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error("Fehler beim Parsen der rclone-Ausgabe: %s", e)
            return None
        except OSError as e:
            logger.error("rclone about Ausfuehrungsfehler: %s", e)
            return None

    def is_overdue(self, hours: int = 24) -> bool:
        """
        Prueft ob der letzte Upload aelter als die angegebene Stundenzahl ist.

        Args:
            hours: Schwellenwert in Stunden (Standard: 24)

        Returns:
            True wenn kein Upload vorhanden oder der letzte Upload aelter als X Stunden ist
        """
        last = self.get_last_successful_upload()
        if last is None:
            # Kein Upload gefunden — gilt als ueberfaellig
            return True

        try:
            last_time = datetime.strptime(last["timestamp"], "%Y-%m-%d %H:%M:%S")
            # Zeitstempel ist lokal (kein Zeitzonen-Info im Log-Format)
            threshold = datetime.now() - timedelta(hours=hours)
            return last_time < threshold
        except (ValueError, KeyError) as e:
            logger.warning("Konnte Upload-Zeitstempel nicht parsen: %s", e)
            return True

    def _count_uploads_in_period(self, days: int = 7) -> int:
        """
        Zaehlt die Anzahl erfolgreicher Uploads innerhalb der letzten X Tage.

        Args:
            days: Zeitraum in Tagen (Standard: 7)

        Returns:
            Anzahl der Uploads im Zeitraum
        """
        cutoff = datetime.now() - timedelta(days=days)
        all_uploads = self._collect_all_uploads()
        count = 0

        for entry in all_uploads:
            try:
                upload_time = datetime.strptime(entry["timestamp"], "%Y-%m-%d %H:%M:%S")
                if upload_time >= cutoff:
                    count += 1
                else:
                    # Liste ist sortiert (neueste zuerst) — abbrechen wenn zu alt
                    break
            except (ValueError, KeyError):
                continue

        return count

    async def get_status_summary(self) -> dict:
        """
        Erstellt eine kombinierte Uebersicht fuer das Dashboard.

        Sammelt letzten Upload, Upload-Historie, Ueberfaelligkeit,
        Speicherverbrauch und Upload-Zaehler in einem Dict.

        Returns:
            Dict mit Keys:
                - last_upload: Dict oder None
                - overdue: bool (>24h seit letztem Upload)
                - recent_uploads: Liste der letzten 10 Uploads
                - storage: Dict mit Speicher-Info oder None
                - upload_count_7d: Anzahl Uploads in den letzten 7 Tagen
        """
        # Log-basierte Daten sammeln (synchron, da nur Datei-I/O)
        last_upload = self.get_last_successful_upload()
        recent_uploads = self.get_recent_uploads(limit=10)
        overdue = self.is_overdue(hours=24)
        upload_count_7d = self._count_uploads_in_period(days=7)

        # Cloud-Speicher abfragen (async, da Subprocess)
        raw_storage = await self.get_cloud_storage_usage()

        # Speicher-Info in benutzerfreundliches Format umwandeln
        storage: Optional[dict] = None
        if raw_storage is not None:
            total_bytes = raw_storage.get("total", 0)
            used_bytes = raw_storage.get("used", 0)
            free_bytes = raw_storage.get("free", 0)

            total_gb = round(total_bytes / (1024 ** 3), 2) if total_bytes else 0.0
            used_gb = round(used_bytes / (1024 ** 3), 2) if used_bytes else 0.0
            free_gb = round(free_bytes / (1024 ** 3), 2) if free_bytes else 0.0
            used_percent = round((used_bytes / total_bytes) * 100, 1) if total_bytes > 0 else 0.0

            storage = {
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": free_gb,
                "used_percent": used_percent,
            }

        summary = {
            "last_upload": last_upload,
            "overdue": overdue,
            "recent_uploads": recent_uploads,
            "storage": storage,
            "upload_count_7d": upload_count_7d,
        }

        logger.debug(
            "Status-Summary erstellt: %d Uploads (7d), ueberfaellig=%s, Speicher=%s",
            upload_count_7d,
            overdue,
            "verfuegbar" if storage else "nicht abfragbar",
        )

        return summary
