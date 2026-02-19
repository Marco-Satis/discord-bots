"""
Server Config Backup Module
Backs up server-specific configuration files to OneDrive
Files that only exist on the server and would be lost otherwise:
  - .env (tokens, passwords, API keys)
  - data/ (player tracker, status message ID, backup history)
  - rclone config
  - sudoers rules
  - systemd service files
"""

import asyncio
import tarfile
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List, Any

from utils.logger import get_logger

logger = get_logger("config_backup")

# Files and directories to backup
CONFIG_BACKUP_ITEMS = [
    # (source_path, archive_name, needs_sudo)
    ("{project_root}/config/.env", "config/.env", False),
    ("{project_root}/config/config.json", "config/config.json", False),
    ("{project_root}/data/", "data/", False),
    ("/home/botuser/.config/rclone/rclone.conf", "rclone/rclone.conf", False),
    ("/etc/sudoers.d/botuser", "systemd/botuser-sudoers", True),
    ("/etc/systemd/system/gameserver-bot.service", "systemd/gameserver-bot.service", True),
    ("/etc/systemd/system/monitor-bot.service", "systemd/monitor-bot.service", True),
    ("/etc/systemd/system/satisfactory.service", "systemd/satisfactory.service", True),
]


class ConfigBackup:
    """
    Backs up server-specific config files to OneDrive.

    Usage:
        config_bk = ConfigBackup(project_root, onedrive_backup)
        success, msg = await config_bk.create_and_upload()
    """

    def __init__(self, project_root: Path, onedrive_backup=None,
                 remote_path: str = "Backups/ServerConfig",
                 max_backups: int = 7):
        self.project_root = project_root
        self.onedrive = onedrive_backup
        self.remote_path = remote_path
        self.max_backups = max_backups
        self.last_backup: Optional[datetime] = None

    async def create_and_upload(self) -> Tuple[bool, str]:
        """
        Create a tar.gz of all config files and upload to OneDrive.
        Returns (success, message)
        """
        if not self.onedrive or not self.onedrive.enabled:
            return False, "OneDrive Backup ist deaktiviert"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"server_config_{timestamp}.tar.gz"

        try:
            # Create archive in temp directory
            with tempfile.TemporaryDirectory() as tmpdir:
                archive_path = Path(tmpdir) / filename

                # Collect files
                files_added = 0
                files_failed = []

                with tarfile.open(str(archive_path), "w:gz") as tar:
                    for source_template, archive_name, needs_sudo in CONFIG_BACKUP_ITEMS:
                        source = source_template.format(
                            project_root=str(self.project_root)
                        )
                        source_path = Path(source)

                        try:
                            if needs_sudo:
                                # Read via sudo for system files
                                content = await self._read_with_sudo(source)
                                if content is not None:
                                    await self._add_bytes_to_tar(
                                        tar, archive_name, content
                                    )
                                    files_added += 1
                                else:
                                    files_failed.append(source)
                            elif source_path.is_dir():
                                # Add directory recursively
                                if source_path.exists():
                                    for file in source_path.rglob("*"):
                                        if file.is_file():
                                            rel = file.relative_to(source_path)
                                            arc_name = f"{archive_name}{rel}"
                                            tar.add(str(file), arcname=arc_name)
                                            files_added += 1
                                else:
                                    files_failed.append(source)
                            elif source_path.exists():
                                tar.add(str(source_path), arcname=archive_name)
                                files_added += 1
                            else:
                                files_failed.append(source)
                        except Exception as e:
                            logger.debug(f"Failed to add {source}: {e}")
                            files_failed.append(source)

                if files_added == 0:
                    return False, "Keine Dateien zum Sichern gefunden"

                # Upload to OneDrive
                size_kb = round(archive_path.stat().st_size / 1024, 1)
                logger.info(
                    f"Config backup created: {filename} "
                    f"({files_added} files, {size_kb} KB)"
                )

                # Use rclone directly to upload to separate folder
                success, msg = await self._upload_to_onedrive(
                    str(archive_path), filename
                )

                if success:
                    self.last_backup = datetime.now()
                    # Rotate old config backups
                    await self._rotate()

                    result_msg = (
                        f"{filename} ({size_kb} KB, {files_added} Dateien)"
                    )
                    if files_failed:
                        result_msg += (
                            f"\n⚠️ {len(files_failed)} Dateien uebersprungen"
                        )
                    return True, result_msg
                else:
                    return False, msg

        except Exception as e:
            logger.error(f"Config backup failed: {e}", exc_info=True)
            return False, f"Fehler: {str(e)}"

    async def _read_with_sudo(self, path: str) -> Optional[bytes]:  # type: ignore
        """Read a system file using sudo"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "cat", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=10
            )
            if proc.returncode == 0:
                return stdout
            return None
        except (asyncio.TimeoutError, OSError) as e:
            logger.debug(f"Failed to read {path} with sudo: {e}")
            return None

    async def _add_bytes_to_tar(self, tar: tarfile.TarFile,
                                 name: str, data: bytes) -> None:  # type: ignore
        """Add raw bytes to a tar archive"""
        import io
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    async def _upload_to_onedrive(self, local_path: str,
                                   filename: str) -> Tuple[bool, str]:  # type: ignore
        """Upload file to OneDrive config backup folder"""
        try:
            remote_target = f"{self.onedrive.remote_name}:{self.remote_path}"
            proc = await asyncio.create_subprocess_exec(
                "rclone", "copyto",
                local_path,
                f"{remote_target}/{filename}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120
            )

            if proc.returncode == 0:
                logger.info(f"Config backup uploaded: {filename}")
                return True, f"Hochgeladen: {filename}"
            else:
                error = stderr.decode("utf-8", errors="ignore")[:200]
                return False, f"Upload fehlgeschlagen: {error}"

        except asyncio.TimeoutError:
            return False, "Upload Timeout"
        except Exception as e:
            return False, f"Upload Fehler: {str(e)}"

    async def _rotate(self) -> None:  # type: ignore
        """Keep only max_backups config backups on OneDrive"""
        try:
            remote_target = f"{self.onedrive.remote_name}:{self.remote_path}"
            proc = await asyncio.create_subprocess_exec(
                "rclone", "lsf", remote_target,
                "--format", "tp", "--separator", "|",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode != 0:
                return

            lines = stdout.decode().strip().split("\n")
            files = []
            for line in lines:
                if "|" in line:
                    parts = line.strip().split("|", 1)
                    if len(parts) == 2:
                        files.append({"time": parts[0], "name": parts[1]})

            if len(files) <= self.max_backups:
                return

            # Sort oldest first, delete excess
            files.sort(key=lambda x: x["time"])
            to_delete = files[:len(files) - self.max_backups]

            for f in to_delete:
                try:
                    del_proc = await asyncio.create_subprocess_exec(
                        "rclone", "deletefile",
                        f"{remote_target}/{f['name']}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(del_proc.communicate(), timeout=30)
                    logger.info(f"Config rotation: deleted {f['name']}")
                except (asyncio.TimeoutError, OSError) as e:
                    logger.debug(f"Failed to delete config backup {f['name']}: {e}")

        except Exception as e:
            logger.debug(f"Config backup rotation error: {e}")

    def get_status(self) -> Any:  # type: ignore
        """Get config backup status"""
        return {
            "remote_path": self.remote_path,
            "max_backups": self.max_backups,
            "last_backup": (
                self.last_backup.isoformat() if self.last_backup else None
            ),
        }
