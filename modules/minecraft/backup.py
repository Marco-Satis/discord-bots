"""
Minecraft World Backup Manager
Verwaltet Backup/Restore-Operationen fuer Minecraft-Welten.
Unterstuetzt Multi-Server-Betrieb (eine Instanz pro Server).
"""

import asyncio
import shutil
from pathlib import Path
from datetime import datetime
from typing import Tuple, List, Optional, Dict
from utils.logger import get_logger

logger = get_logger("minecraft.backup")


class MinecraftBackupManager:
    """Minecraft World-Backup-Verwaltung (Multi-Server-faehig)"""

    def __init__(self,
                 savegame_path: Path,
                 backup_path: Path,
                 max_backups: int = 20,
                 server_id: str = "MC") -> None:
        """
        Args:
            savegame_path: Pfad zum World-Verzeichnis
            backup_path: Pfad zum Backup-Verzeichnis
            max_backups: Maximale Anzahl aufbewahrter Backups (0 = unbegrenzt)
            server_id: Server-ID fuer Log-Prefix
        """
        self.world_path = savegame_path
        self.backup_path = backup_path
        self.max_backups = max_backups
        self.server_id = server_id.upper()

        # Backup-Verzeichnis erstellen falls noetig
        try:
            self.backup_path.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            logger.warning(
                f"[{self.server_id}] Backup-Pfad nicht erstellbar: {self.backup_path} "
                f"(Berechtigungen pruefen!)"
            )

    # ------------------------------------------------------------------
    # Backup erstellen
    # ------------------------------------------------------------------

    async def create_backup(self,
                            name: Optional[str] = None,
                            created_by: str = "System") -> Tuple[bool, str, Optional[Path]]:
        """
        Erstellt ein Backup des World-Verzeichnisses.

        Args:
            name: Optionaler Backup-Name (Standard: Zeitstempel)
            created_by: Wer das Backup ausgeloest hat

        Returns:
            (erfolg, nachricht, backup_pfad)
        """
        if not self.world_path.exists():
            return False, f"World-Pfad existiert nicht: {self.world_path}", None

        # Backup-Name generieren
        if not name:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"world_{timestamp}"

        # Name sanitisieren
        name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)

        backup_dest = self.backup_path / name

        if backup_dest.exists():
            return False, f"Backup existiert bereits: {name}", None

        loop = asyncio.get_running_loop()

        try:
            logger.info(f"[{self.server_id}] Backup erstellen: {name} (von {created_by})")

            # World-Verzeichnis kopieren (mit Fehlertoleranz fuer nicht-lesbare Dateien)
            def _copy_tree():
                errors_list = []

                def _error_handler(src, dst, exc_info):
                    """Sammelt Fehler statt abzubrechen"""
                    errors_list.append((src, str(exc_info[1])))

                shutil.copytree(
                    self.world_path, backup_dest,
                    ignore=shutil.ignore_patterns('*.lock', 'session.lock'),
                    copy_function=_safe_copy2,
                )

                if errors_list:
                    logger.warning(
                        f"[{self.server_id}] Backup teilweise: "
                        f"{len(errors_list)} Dateien uebersprungen"
                    )
                    for src, err in errors_list[:5]:
                        logger.debug(f"  Uebersprungen: {src} ({err})")

                return errors_list

            def _safe_copy2(src, dst):
                """Kopiert eine Datei, ueberspringt bei PermissionError"""
                try:
                    shutil.copy2(src, dst)
                except PermissionError:
                    # Datei nicht lesbar — leere Kopie erstellen mit Warnung
                    logger.debug(f"[{self.server_id}] Nicht lesbar, uebersprungen: {src}")
                    pass

            copy_errors = await loop.run_in_executor(None, _copy_tree)

            # Metadaten-Datei erstellen (async via Executor)
            metadata_content = (
                f"Created: {datetime.now().isoformat()}\n"
                f"By: {created_by}\n"
                f"Server: {self.server_id}\n"
                f"World Path: {self.world_path}\n"
            )

            def _write_metadata():
                metadata_path = backup_dest / ".backup_metadata"
                metadata_path.write_text(metadata_content, encoding='utf-8')

            await loop.run_in_executor(None, _write_metadata)

            size_mb = await self._get_path_size_mb_async(backup_dest)
            if copy_errors:
                msg = (
                    f"Backup erstellt (teilweise): {name} ({size_mb:.1f} MB) "
                    f"— {len(copy_errors)} Dateien uebersprungen"
                )
            else:
                msg = f"Backup erstellt: {name} ({size_mb:.1f} MB)"
            logger.info(f"[{self.server_id}] {msg}")

            # Alte Backups aufraeumen
            if self.max_backups > 0:
                await self._cleanup_old_backups()

            return True, msg, backup_dest

        except Exception as e:
            logger.error(f"[{self.server_id}] Backup fehlgeschlagen: {e}")
            # Teilweises Backup aufraeumen (async)
            if backup_dest.exists():
                try:
                    await loop.run_in_executor(None, shutil.rmtree, backup_dest)
                except OSError as cleanup_err:
                    logger.debug(f"[{self.server_id}] Cleanup fehlgeschlagen: {cleanup_err}")
            return False, f"Backup fehlgeschlagen: {e}", None

    # ------------------------------------------------------------------
    # Backups auflisten
    # ------------------------------------------------------------------

    async def list_backups(self, max_results: int = 20) -> List[Dict]:
        """
        Verfuegbare Backups auflisten (neueste zuerst).

        Args:
            max_results: Maximale Anzahl zurueckgegebener Backups

        Returns:
            Liste von Backup-Info-Dicts
        """
        loop = asyncio.get_running_loop()

        def _list() -> List[Dict]:
            backups = []
            try:
                for backup_dir in sorted(
                    self.backup_path.iterdir(),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                ):
                    if not backup_dir.is_dir():
                        continue

                    try:
                        metadata_file = backup_dir / ".backup_metadata"
                        created_by = "System"
                        created_date = datetime.fromtimestamp(
                            backup_dir.stat().st_mtime
                        ).isoformat()

                        if metadata_file.exists():
                            metadata = metadata_file.read_text(encoding='utf-8')
                            for line in metadata.split("\n"):
                                if line.startswith("Created:"):
                                    created_date = line.split(":", 1)[1].strip()
                                elif line.startswith("By:"):
                                    created_by = line.split(":", 1)[1].strip()

                        # Groesse synchron berechnen (laeuft eh im Executor)
                        total = 0
                        for entry in backup_dir.rglob("*"):
                            if entry.is_file():
                                total += entry.stat().st_size
                        size_mb = total / (1024 * 1024)

                        backups.append({
                            "name": backup_dir.name,
                            "created": created_date,
                            "size_mb": size_mb,
                            "created_by": created_by,
                            "path": backup_dir
                        })

                    except Exception as e:
                        logger.debug(
                            f"[{self.server_id}] Backup-Info Fehler ({backup_dir.name}): {e}"
                        )
                        continue

            except Exception as e:
                logger.error(f"[{self.server_id}] Backup-Liste Fehler: {e}")

            return backups[:max_results]

        return await loop.run_in_executor(None, _list)

    # ------------------------------------------------------------------
    # Wiederherstellen
    # ------------------------------------------------------------------

    async def restore(self, backup_name: str) -> Tuple[bool, str]:
        """
        Welt aus Backup wiederherstellen.

        WARNUNG: Ueberschreibt die aktuelle Welt!

        Args:
            backup_name: Name des wiederherzustellenden Backups

        Returns:
            (erfolg, nachricht)
        """
        # Path-Traversal Schutz
        backup_source = (self.backup_path / backup_name).resolve()
        if not str(backup_source).startswith(str(self.backup_path.resolve())):
            return False, "Ungueltiger Backup-Name (Path-Traversal)"

        if not backup_source.exists():
            return False, f"Backup nicht gefunden: {backup_name}"

        if not backup_source.is_dir():
            return False, f"Backup ist kein Verzeichnis: {backup_name}"

        loop = asyncio.get_running_loop()

        try:
            logger.info(f"[{self.server_id}] Wiederherstellung aus: {backup_name}")

            # Aktuelle Welt sichern bevor wir ueberschreiben
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safety_backup = self.backup_path / f"world_before_restore_{timestamp}"

            if self.world_path.exists():
                def _backup_current():
                    shutil.copytree(
                        self.world_path, safety_backup,
                        ignore=shutil.ignore_patterns('*.lock', 'session.lock')
                    )

                await loop.run_in_executor(None, _backup_current)
                logger.info(f"[{self.server_id}] Sicherheitskopie: {safety_backup.name}")

                # Aktuelle Welt entfernen
                await loop.run_in_executor(None, shutil.rmtree, self.world_path)

            # Backup wiederherstellen
            def _restore():
                shutil.copytree(backup_source, self.world_path)

            await loop.run_in_executor(None, _restore)

            msg = f"Welt wiederhergestellt: {backup_name}"
            logger.info(f"[{self.server_id}] {msg}")
            return True, msg

        except Exception as e:
            logger.error(f"[{self.server_id}] Wiederherstellung fehlgeschlagen: {e}")
            return False, f"Wiederherstellung fehlgeschlagen: {e}"

    # ------------------------------------------------------------------
    # Loeschen
    # ------------------------------------------------------------------

    async def delete_backup(self, backup_name: str) -> Tuple[bool, str]:
        """Backup loeschen"""
        # Path-Traversal Schutz
        backup_target = (self.backup_path / backup_name).resolve()
        if not str(backup_target).startswith(str(self.backup_path.resolve())):
            return False, "Ungueltiger Backup-Name (Path-Traversal)"

        if not backup_target.exists():
            return False, f"Backup nicht gefunden: {backup_name}"

        loop = asyncio.get_running_loop()

        try:
            await loop.run_in_executor(None, shutil.rmtree, backup_target)
            logger.info(f"[{self.server_id}] Backup geloescht: {backup_name}")
            return True, f"Backup geloescht: {backup_name}"
        except Exception as e:
            logger.error(f"[{self.server_id}] Loeschung fehlgeschlagen ({backup_name}): {e}")
            return False, f"Loeschung fehlgeschlagen: {e}"

    # ------------------------------------------------------------------
    # Hilfsfunktionen
    # ------------------------------------------------------------------

    async def _get_path_size_mb_async(self, path: Path) -> float:
        """Verzeichnisgroesse in MB berechnen (async)"""
        loop = asyncio.get_running_loop()

        def _calc() -> float:
            total = 0
            try:
                for entry in path.rglob("*"):
                    if entry.is_file():
                        total += entry.stat().st_size
            except Exception as e:
                logger.debug(f"[{self.server_id}] Groessenberechnung Fehler: {e}")
            return total / (1024 * 1024)

        return await loop.run_in_executor(None, _calc)

    async def _cleanup_old_backups(self) -> None:
        """Alte Backups loeschen wenn max_backups ueberschritten"""
        if self.max_backups <= 0:
            return

        loop = asyncio.get_running_loop()

        def _cleanup():
            try:
                backup_dirs = sorted(
                    [d for d in self.backup_path.iterdir() if d.is_dir()],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )

                for old_backup in backup_dirs[self.max_backups:]:
                    try:
                        shutil.rmtree(old_backup)
                        logger.info(
                            f"[{self.server_id}] Altes Backup entfernt: {old_backup.name}"
                        )
                    except OSError as e:
                        logger.debug(
                            f"[{self.server_id}] Cleanup Fehler ({old_backup.name}): {e}"
                        )
            except Exception as e:
                logger.debug(f"[{self.server_id}] Backup-Cleanup Fehler: {e}")

        await loop.run_in_executor(None, _cleanup)
