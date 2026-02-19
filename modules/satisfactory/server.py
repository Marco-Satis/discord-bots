"""
Satisfactory Dedicated Server management via systemd
Netcup RS 4000 G12 - AMD EPYC 9645
"""

import asyncio
import subprocess
import time
import psutil
from pathlib import Path
from typing import Tuple, Optional, Dict, Any
from utils.logger import get_logger

logger = get_logger("satisfactory.server")

# Allowed systemctl actions (whitelist for security)
ALLOWED_ACTIONS = {"start", "stop", "restart", "status", "is-active"}


class SatisfactoryServer:
    """Manage Satisfactory Dedicated Server via systemd"""

    def __init__(self, service_name: str = "satisfactory.service",
                 server_user: str = "satisfactory",
                 server_path: str = "/home/satisfactory/SatisfactoryDedicatedServer") -> None:
        self.service_name = service_name
        self.server_user = server_user
        self.server_path = Path(server_path)
        self.save_path = (Path(f"/home/{server_user}") / ".config" / "Epic" /
                         "FactoryGame" / "Saved")

    # ------------------------------------------------------------------
    # systemctl helpers
    # ------------------------------------------------------------------

    async def _systemctl(self, action: str, timeout: int = 30, use_sudo: bool = True) -> Tuple[int, str, str]:
        """Run systemctl command asynchronously"""
        # Validate action against whitelist to prevent command injection
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"Invalid systemctl action: {action}. Allowed: {ALLOWED_ACTIONS}")

        try:
            cmd = ["sudo", "/usr/bin/systemctl", action, self.service_name] if use_sudo \
                else ["/usr/bin/systemctl", action, self.service_name]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return proc.returncode, stdout.decode().strip(), stderr.decode().strip()
        except asyncio.TimeoutError:
            logger.error(f"systemctl {action} timed out ({timeout}s)")
            return -1, "", "Timeout"
        except Exception as e:
            logger.error(f"systemctl {action} error: {e}")
            return -1, "", str(e)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def is_running(self) -> bool:
        """Check if server is active via systemd (no sudo needed for read-only)"""
        code, stdout, _ = await self._systemctl("is-active", timeout=10, use_sudo=False)
        return code == 0 and stdout == "active"

    async def get_status(self) -> Dict[str, Any]:
        """Get comprehensive server status"""
        running = await self.is_running()
        info: Dict[str, Any] = {
            "running": running,
            "service": self.service_name,
            "uptime": 0,
            "cpu_percent": 0.0,
            "memory_mb": 0,
            "pid": None
        }
        if running:
            proc_info = self._find_process()
            if proc_info:
                info["pid"] = proc_info["pid"]
                info["uptime"] = proc_info["uptime"]
                info["cpu_percent"] = proc_info["cpu"]
                info["memory_mb"] = proc_info["mem_mb"]
        return info

    def _find_process(self) -> Optional[Dict[str, Any]]:
        """Find FactoryServer process via psutil"""
        try:
            for proc in psutil.process_iter(["name", "username", "pid",
                                              "create_time", "cpu_percent",
                                              "memory_info"]):
                try:
                    name = proc.info.get("name") or ""
                    user = proc.info.get("username") or ""
                    if "FactoryServer" in name and user == self.server_user:
                        return {
                            "pid": proc.info["pid"],
                            "uptime": int(time.time() - proc.info["create_time"]),
                            "cpu": proc.info.get("cpu_percent", 0.0),
                            "mem_mb": (proc.info.get("memory_info").rss // (1024 * 1024)
                                      if proc.info.get("memory_info") else 0)
                        }
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            logger.error(f"Process lookup error: {e}")
        return None

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    async def start(self) -> Tuple[bool, str]:
        """Start server via systemd"""
        if await self.is_running():
            return False, "Server laeuft bereits."

        code, _, stderr = await self._systemctl("start", timeout=60)
        if code != 0:
            return False, f"Start fehlgeschlagen: {stderr}"

        # Wait for process to come up
        for _ in range(10):
            await asyncio.sleep(2)
            if await self.is_running():
                logger.info("Satisfactory server started")
                return True, "Server gestartet!"
        return False, "Start-Befehl gesendet, aber Server nicht aktiv."

    async def stop(self) -> Tuple[bool, str]:
        """Stop server via systemd"""
        if not await self.is_running():
            return False, "Server ist nicht gestartet."

        code, _, stderr = await self._systemctl("stop", timeout=90)
        if code != 0:
            return False, f"Stop fehlgeschlagen: {stderr}"

        # Wait for shutdown
        for _ in range(30):
            await asyncio.sleep(1)
            if not await self.is_running():
                logger.info("Satisfactory server stopped")
                return True, "Server gestoppt!"

        return False, "Stop-Befehl gesendet, Server reagiert nicht."

    async def restart(self) -> Tuple[bool, str]:
        """Restart server via systemd"""
        code, _, stderr = await self._systemctl("restart", timeout=120)
        if code != 0:
            return False, f"Restart fehlgeschlagen: {stderr}"

        await asyncio.sleep(5)
        if await self.is_running():
            logger.info("Satisfactory server restarted")
            return True, "Server neugestartet!"
        return False, "Restart-Befehl gesendet, Server nicht aktiv."

    # ------------------------------------------------------------------
    # Savegame paths
    # ------------------------------------------------------------------

    @property
    def savegame_path(self) -> Path:
        return self.save_path / "SaveGames"

    @property
    def config_path(self) -> Path:
        return self.save_path / "Config" / "LinuxServer"

    @property
    def blueprint_path(self) -> Path:
        return self.save_path / "SaveGames" / "blueprints"
