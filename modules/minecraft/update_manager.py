"""
UpdateManager — Zentraler Orchestrator für das Auto-Update-System

Steuert den kompletten Update-Flow für Minecraft-Server:
  Phase 0: Crash-Recovery (beim Bot-Start)
  Phase 1: Erkennung (CurseForge API)
  Phase 2: Ankündigung (10-Min-Countdown via MCCountdownTimer)
  Phase 3: Vorbereitung (Disk-Check, HAR-Suppress, Server-Stop)
  Phase 4: Backup (World + mods/config Rollback)
  Phase 5: Custom-Dateien sichern
  Phase 6: Update (Download, Hash, Extract, NeoForge, Atomic Swap)
  Phase 7: Verifikation (3 Startversuche, RCON-Check)
  Phase 8: Benachrichtigungen (Discord, DM, E-Mail, In-Game)

Sicherheitsfeatures:
  - asyncio.Lock pro Server (kein paralleles Update)
  - Crash-Recovery: Abgebrochene Updates werden fortgesetzt
  - Atomic Swap: Staging → Produktion
  - Hash-Verifikation: SHA1/MD5 von CurseForge
  - 3-Versuche-System mit automatischem Rollback
  - HAR-Suppress vor dem Countdown (nicht erst beim Stop)
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import discord

from modules.minecraft.file_manager import FileManager, FileManagerError
from modules.minecraft.mc_countdown import MCCountdownTimer
from modules.minecraft.modpack_updater import ModpackUpdater
from modules.minecraft.neoforge_updater import NeoForgeUpdater
from modules.restart_timer import TimerResult
from utils.logger import get_logger

logger = get_logger("minecraft.update_manager")

# Maximale Startversuche bevor Rollback
MAX_START_ATTEMPTS = 3

# RCON-Timeout für Erreichbarkeitsprüfung nach Start (Sekunden)
RCON_WAIT_TIMEOUT = 120

# Preserve-Files Standard-Liste
DEFAULT_PRESERVE_FILES = [
    "server.properties",
    "user_jvm_args.txt",
    "ops.json",
    "whitelist.json",
    "banned-players.json",
    "banned-ips.json",
]


class UpdateManager:
    """
    Zentraler Orchestrator für alle Update-Vorgänge eines MC-Servers.

    Pro Server-Instanz wird ein UpdateManager erstellt.
    Der asyncio.Lock verhindert parallele Updates auf dem gleichen Server.
    """

    def __init__(
        self,
        server_id: str,
        mc_server=None,
        modpack_updater: Optional[ModpackUpdater] = None,
        channel: Optional[discord.TextChannel] = None,
        db=None,
        har=None,
        notifier=None,
        backup_manager=None,
        onedrive_backup=None,
    ):
        self.server_id = server_id.upper()
        self.mc_server = mc_server
        self.modpack_updater = modpack_updater
        self.channel = channel
        self.db = db
        self.har = har                          # Health-Auto-Restart-Instanz
        self.notifier = notifier                # DiscordNotifier
        self.backup_manager = backup_manager    # MinecraftBackupManager
        self.onedrive_backup = onedrive_backup  # OneDriveBackup

        self._lock = asyncio.Lock()
        self._active_timer: Optional[MCCountdownTimer] = None
        self._current_update_id: Optional[int] = None
        self._update_in_progress = False

        # FileManager für Download/Extract/Swap
        self.file_manager = FileManager()

        # NeoForge-Updater (Pfad wird bei run_update gesetzt)
        self._neoforge_updater: Optional[NeoForgeUpdater] = None

        # Preserve-Files (aus ENV oder Default)
        from utils.config import get_env
        preserve_str = get_env(
            f"MC_{self.server_id}_PRESERVE_FILES", ""
        )
        self.preserve_files = (
            [f.strip() for f in preserve_str.split(",") if f.strip()]
            if preserve_str
            else DEFAULT_PRESERVE_FILES
        )

    @property
    def is_updating(self) -> bool:
        """Gibt True zurück wenn ein Update läuft."""
        return self._update_in_progress

    @property
    def active_timer(self) -> Optional[MCCountdownTimer]:
        """Gibt den aktiven Countdown-Timer zurück (oder None)."""
        return self._active_timer

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    async def check_for_update(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Prüft ob ein Update verfügbar ist (delegiert an ModpackUpdater).

        Returns:
            (update_verfügbar, info_dict)
        """
        if not self.modpack_updater or not self.modpack_updater.enabled:
            return False, {"error": "Modpack-Updater nicht konfiguriert"}

        return await self.modpack_updater.check()

    async def run_update(
        self,
        trigger: str = "manual",
        countdown_minutes: int = 10,
        version_info: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Führt ein komplettes Update durch (Phase 0-8).

        Args:
            trigger: Auslöser ("auto_12h", "auto_daily", "manual")
            countdown_minutes: Countdown-Dauer in Minuten
            version_info: Update-Info von check_for_update() (optional)

        Returns:
            (erfolg, ergebnis_dict)
        """
        if self._lock.locked():
            return False, {"error": "Update läuft bereits auf diesem Server"}

        async with self._lock:
            self._update_in_progress = True
            result = {
                "server_id": self.server_id,
                "trigger": trigger,
                "started_at": datetime.now().isoformat(),
                "phases": {},
                "success": False,
                "error": None,
            }

            try:
                # Version-Info holen falls nicht übergeben
                if not version_info:
                    available, version_info = await self.check_for_update()
                    if not available:
                        return False, {"error": "Kein Update verfügbar"}

                server_pack = version_info.get("server_pack", {})
                if not server_pack:
                    return False, {"error": "Kein Server Pack für diese Version verfügbar"}

                new_version = version_info.get("latest", "?")
                download_url = server_pack.get("download_url", "")
                file_size = server_pack.get("file_size", 0)
                hashes = server_pack.get("hashes", {})

                result["new_version"] = new_version
                result["old_version"] = self.modpack_updater.current_version

                # Update-Log in DB erstellen
                update_id = await self._create_update_log(
                    version_info, trigger
                )
                self._current_update_id = update_id

                # === PHASE 2: Ankündigung ===
                await self._set_phase(update_id, "countdown")
                timer_result = await self._run_countdown(
                    countdown_minutes, new_version
                )
                if timer_result == TimerResult.CANCELLED:
                    await self._finalize_update_log(update_id, "cancelled")
                    return False, {"error": "Update durch Benutzer abgebrochen"}
                result["phases"]["countdown"] = "completed"

                # === PHASE 3: Vorbereitung ===
                await self._set_phase(update_id, "preparing")

                # Disk-Space prüfen
                await self.file_manager.check_disk_space(file_size)
                result["phases"]["disk_check"] = "ok"

                # HAR sollte bereits vor dem Countdown unterdrückt worden sein
                # (passiert in _run_countdown), hier nochmal sicherstellen
                if self.har:
                    self.har.suppress(900)

                # Server stoppen
                if self.mc_server:
                    await self._stop_server()
                result["phases"]["server_stop"] = "ok"

                # === PHASE 4: Backup ===
                await self._set_phase(update_id, "backing_up")
                backup_path = await self._create_backups()
                result["phases"]["backup"] = backup_path or "skipped"

                # === PHASE 5: Custom-Dateien sichern ===
                server_path = self._get_server_path()
                custom_files = {}
                if server_path:
                    custom_files = await self.file_manager.backup_custom_files(
                        server_path, self.preserve_files
                    )
                result["phases"]["custom_files"] = len(custom_files)

                # === PHASE 6: Update durchführen ===
                await self._set_phase(update_id, "downloading")
                staging = await self.file_manager.create_staging()

                # Download mit Hash-Verifikation
                zip_path = staging / "server_pack.zip"
                zip_path, dl_meta = await self.file_manager.download_file(
                    url=download_url,
                    dest_path=zip_path,
                    expected_size=file_size,
                    expected_sha1=hashes.get("sha1"),
                    expected_md5=hashes.get("md5"),
                )
                result["phases"]["download"] = dl_meta

                # Extraktion
                await self._set_phase(update_id, "extracting")
                extract_dir = staging / "extracted"
                extract_meta = await self.file_manager.extract_zip(zip_path, extract_dir)
                result["phases"]["extract"] = extract_meta

                # NeoForge-Version prüfen (Installation erst NACH Atomic Swap)
                neoforge_updated = False
                needs_nf, old_nf, new_nf = False, None, None
                if server_path:
                    self._neoforge_updater = NeoForgeUpdater(server_path)
                    needs_nf, old_nf, new_nf = await self._neoforge_updater.needs_update(
                        extract_dir
                    )

                # Custom-Dateien in entpackten Ordner zurückschreiben
                if custom_files and extract_dir.exists():
                    await self.file_manager.restore_custom_files(
                        extract_dir, custom_files
                    )

                # Atomic Swap
                await self._set_phase(update_id, "swapping")
                if server_path:
                    rollback_dir = server_path.parent / f"{server_path.name}_rollback_{update_id}"
                    swap_meta = await self.file_manager.atomic_swap(
                        source_dir=extract_dir,
                        target_dir=server_path,
                        rollback_dir=rollback_dir,
                    )
                    result["phases"]["swap"] = swap_meta

                    # Rollback-Rotation (2 Versionen behalten)
                    await self.file_manager.rotate_rollbacks(
                        server_path.parent,
                        prefix=f"{server_path.name}_rollback_",
                        keep_count=2,
                    )

                    # NeoForge NACH dem Atomic Swap installieren (BUG-6)
                    if needs_nf and new_nf:
                        await self._set_phase(update_id, "neoforge_update")
                        self._neoforge_updater = NeoForgeUpdater(server_path)
                        nf_ok, nf_msg = await self._neoforge_updater.install(new_nf)
                        result["phases"]["neoforge"] = {
                            "old": old_nf, "new": new_nf,
                            "success": nf_ok, "message": nf_msg,
                        }
                        neoforge_updated = nf_ok

                # === PHASE 7: Verifikation (3 Versuche) ===
                await self._set_phase(update_id, "verifying")
                start_success = await self._verify_server_start(update_id)

                if not start_success:
                    # Rollback!
                    logger.error(
                        f"[{self.server_id}] Server startet nicht nach 3 Versuchen "
                        f"— Rollback wird durchgeführt"
                    )
                    await self._perform_rollback(rollback_dir, server_path)
                    await self._finalize_update_log(
                        update_id, "rolled_back",
                        error="Server konnte nach 3 Versuchen nicht gestartet werden",
                    )
                    await self._notify_failure(
                        new_version, "Server startet nicht — Rollback durchgeführt"
                    )
                    result["success"] = False
                    result["error"] = "Rollback nach 3 fehlgeschlagenen Startversuchen"
                    return False, result

                result["phases"]["verify"] = "ok"

                # === PHASE 8: Finalisierung ===
                await self._set_phase(update_id, "finalizing")

                # Version in DB aktualisieren
                if self.modpack_updater and self.db:
                    sp_file_id = server_pack.get("file_id")
                    nf_version = new_nf if neoforge_updated else None
                    await self.modpack_updater.update_version_in_db(
                        self.db,
                        new_file_id=version_info.get("latest_file_id", 0),
                        new_version=new_version,
                        server_pack_file_id=sp_file_id,
                        neoforge_version=nf_version,
                    )

                # Update-Log finalisieren
                await self._finalize_update_log(update_id, "success")

                # Erfolgs-Benachrichtigungen
                await self._notify_success(new_version)

                # Staging aufräumen
                await self.file_manager.cleanup_staging(staging)

                result["success"] = True
                result["completed_at"] = datetime.now().isoformat()
                logger.info(
                    f"[{self.server_id}] Update auf {new_version} erfolgreich abgeschlossen"
                )
                return True, result

            except FileManagerError as e:
                logger.error(f"[{self.server_id}] Update fehlgeschlagen: {e}")
                result["error"] = str(e)
                if self._current_update_id:
                    await self._finalize_update_log(
                        self._current_update_id, "failed", error=str(e)
                    )
                await self._notify_failure(
                    version_info.get("latest", "?") if version_info else "?",
                    str(e),
                )
                # Versuche Server trotzdem zu starten
                await self._safe_start_server()
                return False, result

            except Exception as e:
                logger.error(f"[{self.server_id}] Unerwarteter Fehler: {e}", exc_info=True)
                result["error"] = str(e)
                if self._current_update_id:
                    await self._finalize_update_log(
                        self._current_update_id, "failed", error=str(e)
                    )
                await self._safe_start_server()
                return False, result

            finally:
                self._update_in_progress = False
                self._active_timer = None
                self._current_update_id = None

    async def cancel(self) -> bool:
        """
        Bricht laufendes Update/Countdown ab.

        Returns:
            True wenn erfolgreich abgebrochen
        """
        if self._active_timer and self._active_timer.is_active:
            self._active_timer.cancel()
            logger.info(f"[{self.server_id}] Update-Countdown abgebrochen")
            return True
        return False

    async def check_and_resume(self) -> None:
        """
        Crash-Recovery: Prüft auf abgebrochene Updates beim Bot-Start.

        Wird einmalig beim Bot-Start aufgerufen.
        """
        if not self.db:
            return

        try:
            row = await self.db.fetch_one(
                "SELECT * FROM modpack_updates "
                "WHERE status='in_progress' AND server_id=? "
                "ORDER BY started_at DESC LIMIT 1",
                (self.server_id,),
            )

            if not row:
                return

            attempts = row.get("attempts", 0) if isinstance(row, dict) else 0
            phase = row.get("update_phase", "?") if isinstance(row, dict) else "?"
            update_id = row.get("id", 0) if isinstance(row, dict) else 0

            if attempts >= MAX_START_ATTEMPTS:
                logger.error(
                    f"[{self.server_id}] Abgebrochenes Update nach "
                    f"{attempts} Versuchen gefunden — Rollback wird durchgeführt"
                )
                # Rollback durchführen (BUG-7)
                server_path = self._get_server_path()
                if server_path:
                    rollback_dir = server_path.parent / f"{server_path.name}_rollback_{update_id}"
                    if rollback_dir.exists():
                        await self._perform_rollback(rollback_dir, server_path)
                    else:
                        logger.warning(
                            f"[{self.server_id}] Kein Rollback-Verzeichnis gefunden: {rollback_dir}"
                        )
                await self._finalize_update_log(
                    update_id, "rolled_back",
                    error=f"Crash-Recovery: {attempts} Versuche überschritten (Phase: {phase})",
                )
                await self._notify_failure(
                    row.get("new_version", "?") if isinstance(row, dict) else "?",
                    f"Update nach {attempts} Neustarts fehlgeschlagen — Rollback durchgeführt (Phase: {phase})",
                )
            else:
                logger.warning(
                    f"[{self.server_id}] Abgebrochenes Update gefunden "
                    f"(Phase: {phase}, Versuch {attempts + 1}/{MAX_START_ATTEMPTS})"
                )
                # Versuch erhöhen
                await self.db.execute(
                    "UPDATE modpack_updates SET attempts=? WHERE id=?",
                    (attempts + 1, update_id),
                )

                # Server starten und prüfen (zählt als Versuch)
                if self.mc_server:
                    start_ok = await self._try_start_server()
                    if start_ok:
                        logger.info(
                            f"[{self.server_id}] Crash-Recovery erfolgreich — "
                            f"Server läuft nach Versuch {attempts + 1}"
                        )
                        await self._finalize_update_log(update_id, "success")
                    elif attempts + 1 >= MAX_START_ATTEMPTS:
                        logger.error(
                            f"[{self.server_id}] Crash-Recovery fehlgeschlagen — Rollback wird durchgeführt"
                        )
                        # Rollback durchführen (BUG-7)
                        server_path = self._get_server_path()
                        if server_path:
                            rollback_dir = server_path.parent / f"{server_path.name}_rollback_{update_id}"
                            if rollback_dir.exists():
                                await self._perform_rollback(rollback_dir, server_path)
                            else:
                                logger.warning(
                                    f"[{self.server_id}] Kein Rollback-Verzeichnis gefunden: {rollback_dir}"
                                )
                        await self._finalize_update_log(
                            update_id, "rolled_back",
                            error="Server startet nicht nach Crash-Recovery — Rollback durchgeführt",
                        )
                        await self._notify_failure(
                            row.get("new_version", "?") if isinstance(row, dict) else "?",
                            "Server startet nicht nach Crash-Recovery — Rollback durchgeführt",
                        )

        except Exception as e:
            logger.error(f"[{self.server_id}] Crash-Recovery Fehler: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Status für Discord-Commands und Dashboard."""
        status = {
            "server_id": self.server_id,
            "update_in_progress": self._update_in_progress,
            "timer_active": bool(self._active_timer and self._active_timer.is_active),
        }

        if self.modpack_updater:
            status.update(self.modpack_updater.get_status())

        return status

    # ------------------------------------------------------------------
    # Private Hilfsmethoden
    # ------------------------------------------------------------------

    async def _run_countdown(
        self, minutes: int, new_version: str
    ) -> TimerResult:
        """Startet den Countdown mit MCCountdownTimer."""
        # HAR VOR dem Countdown unterdrücken
        if self.har:
            suppress_time = (minutes * 60) + 900  # Countdown + 15 Min Buffer
            self.har.suppress(suppress_time)
            logger.info(f"HAR unterdrückt für {suppress_time}s")

        self._active_timer = MCCountdownTimer(
            mc_server=self.mc_server,
            channel=self.channel,
            extra_info=f"Update auf {new_version}",
        )

        return await self._active_timer.countdown(
            duration_minutes=minutes,
            action_name="Update",
        )

    async def _stop_server(self) -> None:
        """Server graceful stoppen (BUG-5: RCON stop zuerst, BUG-4: Abbruch statt weitermachen)."""
        if not self.mc_server:
            return

        logger.info(f"[{self.server_id}] Server wird gestoppt...")

        # save-all vor dem Stop
        try:
            await self.mc_server.rcon_command("save-all")
            await asyncio.sleep(5)
        except Exception as e:
            logger.debug(f"save-all fehlgeschlagen: {e}")

        # BUG-5: Erst RCON stop (graceful Java shutdown)
        try:
            await self.mc_server.rcon_command("stop")
            for _ in range(15):
                await asyncio.sleep(2)
                if not await self.mc_server.is_running():
                    logger.info(f"[{self.server_id}] Server nach RCON stop gestoppt")
                    return
        except Exception as e:
            logger.debug(f"RCON stop fehlgeschlagen: {e}")

        # Fallback: systemctl stop
        logger.warning(f"[{self.server_id}] RCON stop nicht erfolgreich — systemctl stop")
        try:
            success, msg = await self.mc_server.stop()
            for _ in range(5):
                await asyncio.sleep(2)
                if not await self.mc_server.is_running():
                    logger.info(f"[{self.server_id}] Server nach systemctl stop gestoppt")
                    return
        except Exception as e:
            logger.error(f"[{self.server_id}] systemctl stop Fehler: {e}")

        # BUG-4: Server immer noch aktiv — Abbruch statt weitermachen
        raise RuntimeError(
            f"Server konnte nicht gestoppt werden nach RCON stop + systemctl stop — Update abgebrochen"
        )

    async def _create_backups(self) -> Optional[str]:
        """World-Backup + Cloud-Upload erstellen."""
        backup_path = None

        if self.backup_manager:
            try:
                result = await self.backup_manager.create_backup()
                backup_path = str(result) if result else None
                logger.info(f"[{self.server_id}] World-Backup erstellt: {backup_path}")
            except Exception as e:
                logger.warning(f"[{self.server_id}] World-Backup fehlgeschlagen: {e}")

        if self.onedrive_backup and backup_path:
            try:
                await self.onedrive_backup.upload(backup_path)
                logger.info(f"[{self.server_id}] Cloud-Upload erfolgreich")
            except Exception as e:
                logger.warning(
                    f"[{self.server_id}] Cloud-Upload fehlgeschlagen "
                    f"(Update wird trotzdem fortgesetzt): {e}"
                )

        return backup_path

    async def _verify_server_start(self, update_id: int) -> bool:
        """
        Versucht den Server 3x zu starten und prüft RCON-Erreichbarkeit.

        Returns:
            True wenn Server erfolgreich gestartet
        """
        for attempt in range(1, MAX_START_ATTEMPTS + 1):
            logger.info(
                f"[{self.server_id}] Startversuch {attempt}/{MAX_START_ATTEMPTS}"
            )

            # Versuchszähler in DB aktualisieren
            if self.db and update_id:
                await self.db.execute(
                    "UPDATE modpack_updates SET attempts=? WHERE id=?",
                    (attempt, update_id),
                )

            success = await self._try_start_server()
            if success:
                logger.info(
                    f"[{self.server_id}] Server erfolgreich gestartet "
                    f"(Versuch {attempt})"
                )
                return True

            if attempt < MAX_START_ATTEMPTS:
                logger.warning(
                    f"[{self.server_id}] Startversuch {attempt} fehlgeschlagen "
                    f"— warte 10s"
                )
                # Server stoppen falls er hängt
                try:
                    await self.mc_server.stop()
                except Exception:
                    pass
                await asyncio.sleep(10)

        return False

    async def _try_start_server(self) -> bool:
        """Startet Server und wartet auf RCON-Erreichbarkeit."""
        if not self.mc_server:
            return False

        try:
            success, msg = await self.mc_server.start()
            if not success:
                logger.warning(f"[{self.server_id}] Start fehlgeschlagen: {msg}")
                return False

            # Warten auf RCON-Erreichbarkeit
            for _ in range(RCON_WAIT_TIMEOUT // 2):
                await asyncio.sleep(2)
                try:
                    response = await self.mc_server.rcon_command("list")
                    if response:
                        return True
                except Exception:
                    pass

            logger.warning(
                f"[{self.server_id}] RCON nach {RCON_WAIT_TIMEOUT}s nicht erreichbar"
            )
            return False

        except Exception as e:
            logger.error(f"[{self.server_id}] Start-Fehler: {e}")
            return False

    async def _perform_rollback(
        self, rollback_dir: Path, server_path: Path
    ) -> None:
        """Stellt den alten Server-Zustand aus dem Rollback wieder her."""
        logger.info(f"[{self.server_id}] Rollback wird durchgeführt...")

        try:
            if rollback_dir.exists():
                # Fehlerhaften neuen Ordner entfernen
                if server_path.exists():
                    proc = await asyncio.create_subprocess_exec(
                        "sudo", "rm", "-rf", str(server_path),
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=60)

                # Rollback zurückschieben
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "mv", str(rollback_dir), str(server_path),
                )
                await asyncio.wait_for(proc.communicate(), timeout=60)

                logger.info(f"[{self.server_id}] Rollback erfolgreich")

                # Server mit alter Version starten
                await self._safe_start_server()
            else:
                logger.error(
                    f"[{self.server_id}] Rollback-Verzeichnis nicht gefunden: "
                    f"{rollback_dir}"
                )
        except Exception as e:
            logger.error(f"[{self.server_id}] Rollback fehlgeschlagen: {e}")

    async def _safe_start_server(self) -> None:
        """Versucht den Server zu starten (best-effort, keine Exception)."""
        try:
            if self.mc_server:
                await self.mc_server.start()
        except Exception as e:
            logger.error(f"[{self.server_id}] Server konnte nicht gestartet werden: {e}")

    def _get_server_path(self) -> Optional[Path]:
        """Gibt den Server-Pfad zurück."""
        if self.mc_server and hasattr(self.mc_server, "server_path"):
            return Path(self.mc_server.server_path)
        return None

    # ------------------------------------------------------------------
    # Datenbank-Operationen
    # ------------------------------------------------------------------

    async def _create_update_log(
        self, version_info: Dict[str, Any], trigger: str
    ) -> int:
        """Erstellt einen Update-Log-Eintrag in SQLite."""
        if not self.db:
            return 0

        try:
            sp = version_info.get("server_pack", {}) or {}
            result = await self.db.execute(
                """INSERT INTO modpack_updates
                   (server_id, old_version, new_version,
                    old_curseforge_id, new_curseforge_id,
                    update_type, status, update_phase,
                    download_url, download_hash_sha1,
                    file_size_bytes, started_at, attempts)
                   VALUES (?, ?, ?, ?, ?, ?, 'in_progress', 'started',
                           ?, ?, ?, ?, 0)""",
                (
                    self.server_id,
                    version_info.get("current", "?"),
                    version_info.get("latest", "?"),
                    version_info.get("current_file_id", 0),
                    version_info.get("latest_file_id", 0),
                    trigger,
                    sp.get("download_url", ""),
                    sp.get("hashes", {}).get("sha1", ""),
                    sp.get("file_size", 0),
                    datetime.now().isoformat(),
                ),
            )
            return result if isinstance(result, int) else 0
        except Exception as e:
            logger.warning(f"Update-Log konnte nicht erstellt werden: {e}")
            return 0

    async def _set_phase(self, update_id: int, phase: str) -> None:
        """Aktualisiert die aktuelle Phase im Update-Log."""
        # RISK-5: HAR-Suppress bei jedem Phasenwechsel verlaengern
        if self.har:
            self.har.suppress(900)

        if self.db and update_id:
            try:
                await self.db.execute(
                    "UPDATE modpack_updates SET update_phase=? WHERE id=?",
                    (phase, update_id),
                )
            except Exception as e:
                logger.debug(f"Phase-Update fehlgeschlagen: {e}")

    async def _finalize_update_log(
        self, update_id: int, status: str, error: Optional[str] = None
    ) -> None:
        """Finalisiert den Update-Log-Eintrag."""
        if not self.db or not update_id:
            return

        try:
            await self.db.execute(
                """UPDATE modpack_updates
                   SET status=?, error_message=?,
                       completed_at=?, update_phase='done'
                   WHERE id=?""",
                (status, error, datetime.now().isoformat(), update_id),
            )
        except Exception as e:
            logger.warning(f"Update-Log Finalisierung fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Benachrichtigungen
    # ------------------------------------------------------------------

    async def _notify_success(self, new_version: str) -> None:
        """Sendet Erfolgs-Benachrichtigungen."""
        # Discord Channel
        if self.channel:
            try:
                embed = discord.Embed(
                    title=f"✅ Update erfolgreich: {self.server_id}",
                    description=f"Server wurde auf **{new_version}** aktualisiert.",
                    color=0x2ecc71,
                    timestamp=datetime.now(),
                )
                await self.channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Erfolgs-Nachricht fehlgeschlagen: {e}")

        # In-Game Banner
        if self._active_timer:
            await self._active_timer.send_success_banner(new_version)

    async def _notify_failure(self, version: str, error: str) -> None:
        """Sendet Fehlschlag-Benachrichtigungen (Channel + Owner-DM)."""
        # Discord Channel
        if self.channel:
            try:
                embed = discord.Embed(
                    title=f"❌ Update fehlgeschlagen: {self.server_id}",
                    description=f"Update auf **{version}** ist fehlgeschlagen.",
                    color=0xe74c3c,
                    timestamp=datetime.now(),
                )
                embed.add_field(name="Fehler", value=error[:1000], inline=False)
                await self.channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Fehler-Nachricht fehlgeschlagen: {e}")

        # Owner-DM
        if self.notifier and hasattr(self.notifier, "send_dm_to_owner"):
            try:
                await self.notifier.send_dm_to_owner(
                    title=f"Update fehlgeschlagen: {self.server_id}",
                    description=(
                        f"Das Update auf **{version}** ist fehlgeschlagen.\n"
                        f"**Fehler:** {error}\n\n"
                        f"Bitte Server manuell prüfen."
                    ),
                )
            except Exception as e:
                logger.debug(f"Owner-DM fehlgeschlagen: {e}")
