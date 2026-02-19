"""
Update Checker Module - SteamCMD Update Detection
Checks for Satisfactory Dedicated Server updates via SteamCMD
"""

import asyncio
import subprocess
import re
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable, Tuple, Dict, Any
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("update_checker")

# Satisfactory Dedicated Server Steam App ID
SATISFACTORY_APP_ID = "1690800"


class UpdateChecker:
    """
    Checks for game server updates using SteamCMD.

    Usage:
        checker = UpdateChecker(steamcmd_path="/usr/games/steamcmd")
        checker.on_update_available = my_callback
        available, info = await checker.check()
    """

    def __init__(self, steamcmd_path: str = "/usr/games/steamcmd",
                 install_dir: str = "/home/satisfactory/SatisfactoryDedicatedServer",
                 app_id: str = SATISFACTORY_APP_ID,
                 server_user: str = "satisfactory") -> None:
        self.steamcmd: str = steamcmd_path
        self.install_dir: str = install_dir
        self.app_id: str = app_id
        self.server_user: str = server_user

        self.last_check: Optional[datetime] = None
        self.last_known_buildid: Optional[str] = None
        self.installed_buildid: Optional[str] = None
        self.update_available: bool = False

        # Callback: async def callback(installed_build, available_build)
        self.on_update_available: Optional[Callable[[str, str], Awaitable]] = None

    async def check(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Check if an update is available.
        Returns (update_available, info_dict)
        """
        self.last_check = datetime.now()
        info = {
            "installed_buildid": None,
            "available_buildid": None,
            "update_available": False,
            "checked_at": self.last_check.isoformat(),
        }

        # Get installed build ID
        installed = await self._get_installed_buildid()
        info["installed_buildid"] = installed
        self.installed_buildid = installed

        # Get available build ID from Steam
        available = await self._get_available_buildid()
        info["available_buildid"] = available
        self.last_known_buildid = available

        if installed and available and installed != available:
            self.update_available = True
            info["update_available"] = True
            logger.info(
                f"Update available! Installed: {installed}, Available: {available}"
            )

            if self.on_update_available:
                try:
                    await self.on_update_available(installed, available)
                except Exception as e:
                    logger.error(f"Update callback error: {e}")
        else:
            self.update_available = False

        return self.update_available, info

    async def _get_installed_buildid(self) -> Optional[str]:
        """Read installed build ID from Steam appmanifest"""
        manifest_name = f"appmanifest_{self.app_id}.acf"

        # Possible manifest locations
        search_paths = [
            Path(self.install_dir) / "steamapps" / manifest_name,
            Path(self.install_dir).parent / "steamapps" / manifest_name,
            Path(f"/home/{self.server_user}/.steam/steamapps/{manifest_name}"),
            Path(f"/home/{self.server_user}/Steam/steamapps/{manifest_name}"),
        ]

        # Try direct read first (works if botuser has permission)
        for path in search_paths:
            try:
                resolved = path.resolve()
                if resolved.exists():
                    content = resolved.read_text()
                    match = re.search(r'"buildid"\s+"(\d+)"', content)
                    if match:
                        return match.group(1)
            except PermissionError:
                # Try with sudo
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "sudo", "-u", self.server_user, "cat", str(path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                    if proc.returncode == 0:
                        content = stdout.decode("utf-8", errors="ignore")
                        match = re.search(r'"buildid"\s+"(\d+)"', content)
                        if match:
                            return match.group(1)
                except (subprocess.SubprocessError, ValueError, OSError, asyncio.TimeoutError) as e:
                    logger.debug(f"Subprocess error reading {path}: {e}")
                    continue
            except (subprocess.SubprocessError, ValueError, OSError, asyncio.TimeoutError) as e:
                logger.debug(f"Error reading {path}: {e}")
                continue

        # Fallback: read via sudo cat for all paths
        for path in search_paths:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "-u", self.server_user, "cat", str(path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                if proc.returncode == 0:
                    content = stdout.decode("utf-8", errors="ignore")
                    match = re.search(r'"buildid"\s+"(\d+)"', content)
                    if match:
                        return match.group(1)
            except (subprocess.SubprocessError, ValueError, OSError, asyncio.TimeoutError) as e:
                logger.debug(f"Fallback read failed for {path}: {e}")
                continue

        return None

    async def _get_available_buildid(self) -> Optional[str]:
        """Check Steam for the latest available build ID"""
        try:
            proc = await asyncio.create_subprocess_exec(
                self.steamcmd,
                "+login", "anonymous",
                "+app_info_update", "1",
                "+app_info_print", self.app_id,
                "+quit",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            output = stdout.decode("utf-8", errors="ignore")

            # Find the public branch buildid
            # Look for "branches" section, then "public" branch
            in_branches = False
            in_public = False
            for line in output.split("\n"):
                stripped = line.strip()
                if '"branches"' in stripped:
                    in_branches = True
                elif in_branches and '"public"' in stripped:
                    in_public = True
                elif in_public and '"buildid"' in stripped:
                    match = re.search(r'"buildid"\s+"(\d+)"', stripped)
                    if match:
                        return match.group(1)
                elif in_public and stripped == "}":
                    break

        except asyncio.TimeoutError:
            logger.warning("SteamCMD update check timed out")
        except Exception as e:
            logger.error(f"SteamCMD update check failed: {e}")

        return None

    async def perform_update(self, server: Any) -> Tuple[bool, str]:
        """
        Perform the actual update via SteamCMD.
        Server should be stopped before calling this.

        Returns (success, message)
        """
        logger.info("Starting server update via SteamCMD...")

        try:
            # Run as server_user for correct file ownership
            cmd = [
                "sudo", "-u", self.server_user,
                self.steamcmd,
                "+force_install_dir", self.install_dir,
                "+login", "anonymous",
                "+app_update", self.app_id, "validate",
                "+quit",
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
            output = stdout.decode("utf-8", errors="ignore")

            if proc.returncode == 0:
                # Check if update actually happened
                if "already up to date" in output.lower():
                    return True, "Server ist bereits aktuell"
                elif "success" in output.lower() or "fully installed" in output.lower():
                    self.update_available = False
                    return True, "Update erfolgreich installiert"
                else:
                    return True, "SteamCMD abgeschlossen"
            else:
                error = stderr.decode("utf-8", errors="ignore")
                return False, f"SteamCMD Fehler (Code {proc.returncode}): {error[:200]}"

        except asyncio.TimeoutError:
            return False, "Update Timeout (>10 Minuten)"
        except Exception as e:
            return False, f"Update Fehler: {str(e)}"
