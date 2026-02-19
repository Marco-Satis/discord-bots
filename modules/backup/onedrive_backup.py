"""
OneDrive Backup Module - Cloud Backup via rclone
Uploads backups to OneDrive with rotation management
"""

import json
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any

from utils.logger import get_logger

logger = get_logger("onedrive_backup")


class OneDriveBackup:
    """
    Manages cloud backups to OneDrive using rclone.

    Prerequisites:
        - rclone must be installed: sudo apt install rclone
        - rclone must be configured with a remote named 'onedrive'
          (run: rclone config)

    Usage:
        cloud = OneDriveBackup(remote_name="onedrive", remote_path="SatisfactoryBackups")
        success = await cloud.upload("/path/to/backup.tar.gz")
        await cloud.rotate(max_backups=10)
    """

    def __init__(self, remote_name: str = "onedrive",
                 remote_path: str = "SatisfactoryBackups",
                 max_cloud_backups: int = 10,
                 enabled: bool = True):
        self.remote_name = remote_name
        self.remote_path = remote_path
        self.max_cloud_backups = max_cloud_backups
        self.enabled = enabled

        self.last_upload: Optional[datetime] = None
        self.last_upload_file: Optional[str] = None

    @property
    def remote_target(self) -> str:  # type: ignore
        return f"{self.remote_name}:{self.remote_path}"

    async def is_configured(self) -> bool:
        """Check if rclone is installed and remote is configured"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "rclone", "listremotes",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            remotes = stdout.decode().strip().split("\n")
            return f"{self.remote_name}:" in remotes
        except FileNotFoundError:
            logger.warning("rclone not installed")
            return False
        except Exception as e:
            logger.error(f"rclone check failed: {e}")
            return False

    async def upload(self, local_path: str) -> Tuple[bool, str]:
        """
        Upload a backup file to OneDrive.
        Returns (success, message)
        """
        if not self.enabled:
            return False, "OneDrive Backup ist deaktiviert"

        path = Path(local_path)
        if not path.exists():
            return False, f"Datei nicht gefunden: {local_path}"

        logger.info(f"Uploading {path.name} to {self.remote_target}/")

        try:
            proc = await asyncio.create_subprocess_exec(
                "rclone", "copyto",
                str(path),
                f"{self.remote_target}/{path.name}",
                "--progress",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)

            if proc.returncode == 0:
                self.last_upload = datetime.now()
                self.last_upload_file = path.name

                # Size info
                size_mb = path.stat().st_size / 1024 / 1024
                logger.info(f"Upload successful: {path.name} ({size_mb:.1f} MB)")
                return True, f"{path.name} ({size_mb:.1f} MB) hochgeladen"
            else:
                error = stderr.decode("utf-8", errors="ignore")[:200]
                logger.error(f"Upload failed: {error}")
                return False, f"Upload fehlgeschlagen: {error}"

        except asyncio.TimeoutError:
            return False, "Upload Timeout (>10 Minuten)"
        except FileNotFoundError:
            return False, "rclone nicht installiert"
        except Exception as e:
            return False, f"Upload Fehler: {str(e)}"

    async def rotate(self) -> Tuple[bool, str]:
        """
        Rotate cloud backups, keeping only max_cloud_backups.
        Deletes oldest files first.
        Returns (success, message)
        """
        if not self.enabled:
            return False, "OneDrive Backup ist deaktiviert"

        try:
            # List files in remote
            proc = await asyncio.create_subprocess_exec(
                "rclone", "lsf",
                self.remote_target,
                "--format", "tp",
                "--separator", "|",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode != 0:
                return False, "Konnte Cloud-Dateien nicht auflisten"

            # Parse file list (format: time|path)
            lines = stdout.decode().strip().split("\n")
            files = []
            for line in lines:
                if "|" in line:
                    parts = line.strip().split("|", 1)
                    if len(parts) == 2:
                        files.append({"time": parts[0], "name": parts[1]})

            if len(files) <= self.max_cloud_backups:
                return True, f"{len(files)} Cloud-Backups (Limit: {self.max_cloud_backups})"

            # Sort by time (oldest first) and delete excess
            files.sort(key=lambda x: x["time"])
            to_delete = files[:len(files) - self.max_cloud_backups]

            deleted = 0
            for f in to_delete:
                try:
                    del_proc = await asyncio.create_subprocess_exec(
                        "rclone", "deletefile",
                        f"{self.remote_target}/{f['name']}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(del_proc.communicate(), timeout=30)
                    if del_proc.returncode == 0:
                        deleted += 1
                        logger.info(f"Cloud rotation: deleted {f['name']}")
                except (subprocess.SubprocessError, OSError, asyncio.TimeoutError) as e:
                    logger.debug(f"Failed to delete cloud backup {f['name']}: {e}")

            msg = f"{deleted} alte Cloud-Backups geloescht, {len(files) - deleted} verbleibend"
            return True, msg

        except Exception as e:
            return False, f"Rotation Fehler: {str(e)}"

    async def list_backups(self) -> Tuple[bool, List[Dict[str, Any]]]:
        """List all backups in cloud storage"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "rclone", "lsjson",
                self.remote_target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode != 0:
                return False, []

            files = json.loads(stdout.decode())
            result = []
            for f in files:
                if not f.get("IsDir", False):
                    result.append({
                        "name": f["Name"],
                        "size_mb": round(f.get("Size", 0) / 1024 / 1024, 1),
                        "modified": f.get("ModTime", ""),
                    })

            result.sort(key=lambda x: x["modified"], reverse=True)
            return True, result

        except Exception as e:
            logger.error(f"List backups failed: {e}")
            return False, []

    def get_status(self) -> Dict[str, Any]:  # type: ignore
        """Get OneDrive backup status"""
        return {
            "enabled": self.enabled,
            "remote": self.remote_target,
            "max_backups": self.max_cloud_backups,
            "last_upload": self.last_upload.isoformat() if self.last_upload else None,
            "last_file": self.last_upload_file,
        }
