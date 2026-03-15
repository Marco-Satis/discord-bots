"""
Stats Collector — Sammelt periodisch System- und Server-Metriken.

Laeuft als Hintergrund-Task im Monitor Bot und schreibt Daten
in die SQLite-Datenbank (Tabelle stats_history).
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from modules.database.db_manager import get_db
from utils.config import DATA_DIR, MONITOR_DATA_DIR, get_config
from utils.logger import get_logger

logger = get_logger(__name__)

# Maximale Anzahl Eintraege im Ringbuffer
# 30 Tage * 24 Stunden * 60 Minuten / 5 Minuten Intervall = 8640
MAX_ENTRIES = 8640


class StatsCollector:
    """
    Sammelt periodisch System- und Server-Metriken und speichert
    sie in der SQLite-Datenbank (stats_history-Tabelle).
    In-Memory-Ringbuffer dient als Cache fuer schnellen Zugriff.
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

        logger.info(
            f"StatsCollector initialisiert — Intervall: {self.interval}s"
        )

    def _save_history(self, entry: dict) -> None:
        """
        Fuegt einen neuen Eintrag zum In-Memory-Ringbuffer hinzu (Cache).

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
            # NUR *_status.json Dateien lesen (vom StatusWriter erzeugt)
            # Andere Dateien (*_players.json, events.json, stats_history.json) ignorieren
            # bot_status.json explizit ausschliessen (kein Gameserver)
            for json_file in sorted(MONITOR_DATA_DIR.glob("*_status.json")):
                # bot_status.json ist Monitor-Bot-Status, kein Gameserver
                if json_file.stem == "bot_status":
                    continue

                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    if not isinstance(data, dict):
                        continue

                    # Spieleranzahl als int sicherstellen (players.json hat Listen)
                    players_val = data.get("players", 0)
                    if isinstance(players_val, list):
                        players_val = len(players_val)

                    server_entry = {
                        "id": data.get("id", json_file.stem.replace("_status", "")),
                        "status": data.get("status", "unknown"),
                        "players": players_val,
                        "cpu_percent": data.get("cpu_percent", 0.0),
                        "ram_mb": data.get("memory_mb", data.get("ram_mb", 0)),
                    }
                    servers.append(server_entry)

                except (json.JSONDecodeError, IOError) as e:
                    logger.debug(f"Konnte {json_file.name} nicht lesen: {e}")

        except Exception as e:
            logger.warning(f"Fehler beim Lesen der Server-Status-Dateien: {e}")

        return servers

    async def _insert_into_db(self, entry: dict) -> None:
        """
        Fuegt einen Metrik-Eintrag in die SQLite stats_history-Tabelle ein.

        Args:
            entry: Der Metrik-Eintrag mit timestamp, system und servers
        """
        try:
            db = await get_db()
            system = entry.get("system", {})
            server_data = json.dumps(entry.get("servers", []), ensure_ascii=False)

            await db.execute(
                """
                INSERT INTO stats_history
                    (timestamp, cpu_percent, ram_percent, ram_used_gb,
                     disk_percent, disk_used_gb, server_data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry["timestamp"],
                    system.get("cpu_percent", 0.0),
                    system.get("ram_percent", 0.0),
                    system.get("ram_used_gb", 0.0),
                    system.get("disk_percent", 0.0),
                    system.get("disk_used_gb", 0.0),
                    server_data,
                ),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Fehler beim SQLite-INSERT (stats_history): {e}")

    async def collect_once(self) -> dict:
        """
        Fuehrt eine einzelne Sammelrunde durch — sammelt System- und
        Server-Metriken, speichert in SQLite und im In-Memory-Cache.

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

        # In In-Memory-Ringbuffer einfuegen (Cache)
        self._save_history(entry)

        # In SQLite-Datenbank schreiben
        await self._insert_into_db(entry)

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
        """Stoppt die periodische Sammlung."""
        self._running = False

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

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
        Gibt die In-Memory Stats-Historie zurueck (Cache).
        Fuer DB-Abfragen get_entries_from_db() verwenden.

        Returns:
            Dict mit 'entries' (Liste) und 'max_entries' (int)
        """
        return self._history

    def get_entries(self) -> list[dict]:
        """
        Gibt die Eintrags-Liste aus dem In-Memory-Cache zurueck.
        Fuer DB-Abfragen get_entries_from_db() verwenden.

        Returns:
            Liste aller gecachten Metrik-Eintraege
        """
        return self._history.get("entries", [])

    async def get_entries_from_db(self, limit: int = 8640) -> list[dict]:
        """
        Liest Metrik-Eintraege direkt aus der SQLite-Datenbank.
        Primaere Lesemethode fuer Dashboard-Abfragen.

        Args:
            limit: Maximale Anzahl Eintraege (Standard: 8640 = 30 Tage)

        Returns:
            Liste von Metrik-Eintraegen im gleichen Format wie get_entries()
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                """
                SELECT timestamp, cpu_percent, ram_percent, ram_used_gb,
                       disk_percent, disk_used_gb, server_data
                FROM stats_history
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()

            if not rows:
                logger.debug("stats_history-Tabelle leer")
                return self._history.get("entries", [])

            entries = []
            for row in reversed(rows):  # Chronologisch aufsteigend
                try:
                    servers = json.loads(row[6]) if row[6] else []
                except (json.JSONDecodeError, TypeError):
                    servers = []

                entries.append({
                    "timestamp": row[0],
                    "system": {
                        "cpu_percent": row[1] or 0.0,
                        "ram_percent": row[2] or 0.0,
                        "ram_used_gb": row[3] or 0.0,
                        "disk_percent": row[4] or 0.0,
                        "disk_used_gb": row[5] or 0.0,
                    },
                    "servers": servers,
                })

            return entries

        except Exception as e:
            logger.error(f"Fehler beim Lesen aus stats_history-DB: {e}")
            return self._history.get("entries", [])

    def get_entry_count(self) -> int:
        """Gibt die Anzahl der gespeicherten Eintraege im Cache zurueck."""
        return len(self._history.get("entries", []))
