"""
Phase 13c: Stats Collector — Sammelt periodisch System- und Server-Metriken.

Laeuft als Hintergrund-Task im Monitor Bot und schreibt Daten
in data/monitor/stats_history.json (Ringbuffer, max. 30 Tage).
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from utils.config import DATA_DIR, MONITOR_DATA_DIR, get_config
from utils.logger import get_logger

logger = get_logger(__name__)

# Pfad fuer die persistierte Stats-Historie
STATS_HISTORY_FILE = MONITOR_DATA_DIR / "stats_history.json"

# Maximale Anzahl Eintraege im Ringbuffer
# 30 Tage * 24 Stunden * 60 Minuten / 5 Minuten Intervall = 8640
MAX_ENTRIES = 8640


class StatsCollector:
    """
    Sammelt periodisch System- und Server-Metriken und speichert
    sie in einem Ringbuffer (JSON-Datei). Wird als Hintergrund-Task
    im Monitor Bot gestartet.
    """

    def __init__(self, interval: int = 300) -> None:
        """
        Initialisiert den Stats Collector.

        Args:
            interval: Sammel-Intervall in Sekunden (Standard: 300 = 5 Minuten)
        """
        self.interval = interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._history: dict = {
            "entries": [],
            "max_entries": MAX_ENTRIES,
        }

        # Sicherstellen, dass das Datenverzeichnis existiert
        MONITOR_DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Vorhandene Historie beim Start laden
        self._load_history()
        logger.info(
            f"StatsCollector initialisiert — Intervall: {self.interval}s, "
            f"vorhandene Eintraege: {len(self._history['entries'])}"
        )

    def _load_history(self) -> None:
        """Laedt die bestehende Stats-Historie von der Festplatte."""
        try:
            if STATS_HISTORY_FILE.exists():
                with open(STATS_HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "entries" in data:
                    self._history = data
                    # max_entries aktualisieren falls sich der Wert geaendert hat
                    self._history["max_entries"] = MAX_ENTRIES
                    logger.info(
                        f"Stats-Historie geladen: {len(self._history['entries'])} Eintraege"
                    )
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.error(f"Fehler beim Laden der Stats-Historie: {e}")
            # Bei Fehler mit leerer Historie starten
            self._history = {"entries": [], "max_entries": MAX_ENTRIES}

    async def _save_history_async(self) -> None:
        """Speichert die Stats-Historie asynchron auf die Festplatte."""
        try:
            await asyncio.to_thread(self._save_history_sync)
        except Exception as e:
            logger.error(f"Fehler beim asynchronen Speichern der Stats-Historie: {e}")

    def _save_history_sync(self) -> None:
        """Synchrones Speichern der Stats-Historie (fuer asyncio.to_thread)."""
        try:
            with open(STATS_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)
        except (IOError, OSError) as e:
            logger.error(f"Fehler beim Speichern der Stats-Historie: {e}")

    def _save_history(self, entry: dict) -> None:
        """
        Fuegt einen neuen Eintrag zum Ringbuffer hinzu und trimmt alte Eintraege.

        Args:
            entry: Der neue Metrik-Eintrag mit Timestamp, System- und Server-Daten
        """
        self._history["entries"].append(entry)

        # Ringbuffer: Aelteste Eintraege entfernen wenn Maximum ueberschritten
        max_entries = self._history.get("max_entries", MAX_ENTRIES)
        if len(self._history["entries"]) > max_entries:
            overflow = len(self._history["entries"]) - max_entries
            self._history["entries"] = self._history["entries"][overflow:]
            logger.debug(f"Ringbuffer getrimmt: {overflow} alte Eintraege entfernt")

    def _collect_system_stats(self) -> dict:
        """
        Sammelt aktuelle System-Metriken (CPU, RAM, Disk) via psutil.

        Returns:
            Dict mit cpu_percent, ram_percent, ram_used_gb, disk_percent, disk_used_gb
        """
        stats = {
            "cpu_percent": 0.0,
            "ram_percent": 0.0,
            "ram_used_gb": 0.0,
            "disk_percent": 0.0,
            "disk_used_gb": 0.0,
        }

        try:
            import psutil

            # CPU-Auslastung (kurzes Messintervall)
            stats["cpu_percent"] = round(psutil.cpu_percent(interval=1.0), 1)

            # RAM-Auslastung
            mem = psutil.virtual_memory()
            stats["ram_percent"] = round(mem.percent, 1)
            stats["ram_used_gb"] = round(mem.used / (1024 ** 3), 1)

            # Festplatten-Auslastung
            disk = psutil.disk_usage("/")
            stats["disk_percent"] = round(disk.percent, 1)
            stats["disk_used_gb"] = round(disk.used / (1024 ** 3), 1)

        except ImportError:
            logger.debug("psutil nicht installiert — System-Stats nicht verfuegbar")
        except Exception as e:
            logger.warning(f"Fehler beim Sammeln der System-Stats: {e}")

        return stats

    def _collect_server_stats(self) -> list[dict]:
        """
        Liest Server-Status-Daten aus den JSON-Dateien in data/monitor/.

        Returns:
            Liste von Dicts mit Server-ID, Status, Spieleranzahl, CPU und RAM
        """
        servers = []

        if not MONITOR_DATA_DIR.exists():
            logger.debug(f"Monitor-Datenverzeichnis nicht gefunden: {MONITOR_DATA_DIR}")
            return servers

        try:
            for json_file in sorted(MONITOR_DATA_DIR.glob("*.json")):
                # stats_history.json selbst ueberspringen
                if json_file.name == "stats_history.json":
                    continue

                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    if not isinstance(data, dict):
                        continue

                    server_entry = {
                        "id": data.get("id", json_file.stem),
                        "status": data.get("status", "unknown"),
                        "players": data.get("players", 0),
                        "cpu_percent": data.get("cpu_percent", 0.0),
                        "ram_mb": data.get("memory_mb", data.get("ram_mb", 0)),
                    }
                    servers.append(server_entry)

                except (json.JSONDecodeError, IOError) as e:
                    logger.debug(f"Konnte {json_file.name} nicht lesen: {e}")

        except Exception as e:
            logger.warning(f"Fehler beim Lesen der Server-Status-Dateien: {e}")

        return servers

    async def collect_once(self) -> dict:
        """
        Fuehrt eine einzelne Sammelrunde durch — sammelt System- und
        Server-Metriken, speichert den Eintrag im Ringbuffer.

        Returns:
            Der erstellte Metrik-Eintrag
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        # System-Stats in einem Thread sammeln (CPU-Messung blockiert kurz)
        system_stats = await asyncio.to_thread(self._collect_system_stats)

        # Server-Stats aus Dateien lesen
        server_stats = await asyncio.to_thread(self._collect_server_stats)

        entry = {
            "timestamp": timestamp,
            "system": system_stats,
            "servers": server_stats,
        }

        # In Ringbuffer einfuegen
        self._save_history(entry)

        # Asynchron auf Festplatte schreiben
        await self._save_history_async()

        logger.debug(
            f"Stats gesammelt — CPU: {system_stats['cpu_percent']}%, "
            f"RAM: {system_stats['ram_percent']}%, "
            f"Server: {len(server_stats)}"
        )

        return entry

    async def start(self) -> None:
        """Startet die periodische Sammlung als Hintergrund-Task."""
        if self._running:
            logger.warning("StatsCollector laeuft bereits")
            return

        self._running = True
        self._task = asyncio.create_task(self._collection_loop())
        logger.info(f"StatsCollector gestartet — Intervall: {self.interval}s")

    async def stop(self) -> None:
        """Stoppt die periodische Sammlung und speichert den aktuellen Stand."""
        self._running = False

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Abschliessend nochmal speichern
        await self._save_history_async()
        logger.info("StatsCollector gestoppt")

    async def _collection_loop(self) -> None:
        """Interne Sammelschleife — laeuft bis stop() aufgerufen wird."""
        logger.info("Stats-Sammelschleife gestartet")

        while self._running:
            try:
                await self.collect_once()
            except Exception as e:
                logger.error(f"Fehler in der Sammelschleife: {e}", exc_info=True)

            # Warten bis zum naechsten Intervall
            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break

        logger.info("Stats-Sammelschleife beendet")

    def get_history(self) -> dict:
        """
        Gibt die gesamte Stats-Historie zurueck.

        Returns:
            Dict mit 'entries' (Liste) und 'max_entries' (int)
        """
        return self._history

    def get_entries(self) -> list[dict]:
        """
        Gibt nur die Eintrags-Liste zurueck.

        Returns:
            Liste aller gesammelten Metrik-Eintraege
        """
        return self._history.get("entries", [])

    def get_entry_count(self) -> int:
        """Gibt die Anzahl der gespeicherten Eintraege zurueck."""
        return len(self._history.get("entries", []))
