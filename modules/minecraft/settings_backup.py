"""
Minecraft Server-Properties Backup/Restore
Sichert und stellt server.properties-Dateien wieder her.
Unterstuetzt Multi-Server-Betrieb (eine Instanz pro Server).
"""

import asyncio
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any
from utils.logger import get_logger

logger = get_logger("minecraft.settings_backup")


class MinecraftSettingsBackup:
    """Backup/Restore fuer Minecraft server.properties"""

    def __init__(self,
                 server_path: Path,
                 backup_dir: Path,
                 server_id: str = "MC",
                 max_backups: int = 20) -> None:
        """
        Args:
            server_path: Pfad zum Server-Verzeichnis (enthaelt server.properties)
            backup_dir: Pfad zum Backup-Verzeichnis fuer Properties
            server_id: Server-ID fuer Log-Prefix
            max_backups: Maximale Anzahl aufbewahrter Settings-Backups
        """
        self.server_path = server_path
        self.properties_file = server_path / "server.properties"
        self.backup_dir = backup_dir
        self.server_id = server_id.upper()
        self.max_backups = max_backups

        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            logger.warning(
                f"[{self.server_id}] Settings-Backup-Pfad nicht erstellbar: "
                f"{self.backup_dir}"
            )

    # ------------------------------------------------------------------
    # Backup erstellen
    # ------------------------------------------------------------------

    async def save_settings(self,
                            name: Optional[str] = None,
                            created_by: str = "System") -> Tuple[bool, str, Optional[Path]]:
        """
        Erstellt ein Backup der server.properties.

        Args:
            name: Optionaler Backup-Name (Standard: Zeitstempel)
            created_by: Wer das Backup ausgeloest hat

        Returns:
            (erfolg, nachricht, backup_pfad)
        """
        if not self.properties_file.exists():
            return False, f"server.properties nicht gefunden: {self.properties_file}", None

        # Dateinamen generieren
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if name:
            safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
            filename = f"settings_{safe_name}_{timestamp}.properties"
        else:
            filename = f"settings_{timestamp}.properties"

        backup_dest = self.backup_dir / filename

        if backup_dest.exists():
            return False, f"Backup existiert bereits: {filename}", None

        loop = asyncio.get_running_loop()

        try:
            logger.info(
                f"[{self.server_id}] Settings-Backup: {filename} (von {created_by})"
            )

            # Datei kopieren
            await loop.run_in_executor(
                None, shutil.copy2, self.properties_file, backup_dest
            )

            size_kb = backup_dest.stat().st_size / 1024
            msg = f"Settings gesichert: {filename} ({size_kb:.1f} KB)"
            logger.info(f"[{self.server_id}] {msg}")

            # Alte Backups aufraeumen
            if self.max_backups > 0:
                await self._rotate()

            return True, msg, backup_dest

        except Exception as e:
            logger.error(f"[{self.server_id}] Settings-Backup fehlgeschlagen: {e}")
            # Teilweises Backup aufraeumen
            if backup_dest.exists():
                try:
                    backup_dest.unlink()
                except OSError:
                    pass
            return False, f"Settings-Backup fehlgeschlagen: {e}", None

    # ------------------------------------------------------------------
    # Wiederherstellen
    # ------------------------------------------------------------------

    async def restore_settings(self, filename: str) -> Tuple[bool, str]:
        """
        Stellt server.properties aus Backup wieder her.

        WARNUNG: Server-Neustart erforderlich nach Wiederherstellung!

        Args:
            filename: Name der Backup-Datei

        Returns:
            (erfolg, nachricht)
        """
        # Path-Traversal Schutz
        safe_name = Path(filename).name
        backup_source = (self.backup_dir / safe_name).resolve()
        if not str(backup_source).startswith(str(self.backup_dir.resolve())):
            return False, "Ungueltiger Dateiname (Path-Traversal)"

        if not backup_source.exists():
            return False, f"Backup nicht gefunden: {safe_name}"

        loop = asyncio.get_running_loop()

        try:
            logger.info(
                f"[{self.server_id}] Settings-Wiederherstellung aus: {safe_name}"
            )

            # Aktuelle Properties sichern bevor wir ueberschreiben
            if self.properties_file.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safety_backup = self.backup_dir / f"settings_before_restore_{timestamp}.properties"
                await loop.run_in_executor(
                    None, shutil.copy2, self.properties_file, safety_backup
                )
                logger.info(
                    f"[{self.server_id}] Sicherheitskopie erstellt: {safety_backup.name}"
                )

            # Backup zurueckkopieren
            await loop.run_in_executor(
                None, shutil.copy2, backup_source, self.properties_file
            )

            msg = (
                f"Settings wiederhergestellt: {safe_name}\n"
                f"⚠️ Server-Neustart erforderlich!"
            )
            logger.info(f"[{self.server_id}] {msg}")
            return True, msg

        except Exception as e:
            logger.error(
                f"[{self.server_id}] Settings-Wiederherstellung fehlgeschlagen: {e}"
            )
            return False, f"Wiederherstellung fehlgeschlagen: {e}"

    # ------------------------------------------------------------------
    # Auflisten
    # ------------------------------------------------------------------

    def list_backups(self) -> List[Dict[str, Any]]:
        """Verfuegbare Settings-Backups auflisten (neueste zuerst)"""
        backups: List[Dict[str, Any]] = []
        try:
            for f in sorted(
                self.backup_dir.glob("settings_*.properties"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            ):
                try:
                    stat = f.stat()
                    backups.append({
                        "filename": f.name,
                        "timestamp": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "size_kb": stat.st_size / 1024,
                    })
                except OSError as e:
                    logger.debug(f"[{self.server_id}] Backup-Info Fehler ({f.name}): {e}")
                    backups.append({
                        "filename": f.name,
                        "timestamp": "?",
                        "size_kb": 0,
                    })
        except Exception as e:
            logger.error(f"[{self.server_id}] Settings-Backup-Liste Fehler: {e}")
        return backups

    # ------------------------------------------------------------------
    # Hilfsfunktionen
    # ------------------------------------------------------------------

    async def _rotate(self) -> None:
        """Alte Settings-Backups loeschen wenn max_backups ueberschritten"""
        if self.max_backups <= 0:
            return

        loop = asyncio.get_running_loop()

        def _cleanup():
            try:
                files = sorted(
                    list(self.backup_dir.glob("settings_*.properties")),
                    key=lambda p: p.stat().st_mtime
                )
                for old_file in files[:-self.max_backups] if len(files) > self.max_backups else []:
                    try:
                        old_file.unlink()
                        logger.info(
                            f"[{self.server_id}] Altes Settings-Backup entfernt: {old_file.name}"
                        )
                    except OSError as e:
                        logger.debug(
                            f"[{self.server_id}] Settings-Rotation Fehler ({old_file.name}): {e}"
                        )
            except Exception as e:
                logger.debug(f"[{self.server_id}] Settings-Backup-Cleanup Fehler: {e}")

        await loop.run_in_executor(None, _cleanup)
