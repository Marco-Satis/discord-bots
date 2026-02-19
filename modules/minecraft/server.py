"""
Minecraft Dedicated Server management via systemd
Supports both Java and Bedrock editions
"""

import asyncio
import subprocess
import time
import psutil
import configparser
from pathlib import Path
from typing import Tuple, Optional, Dict, List
from utils.logger import get_logger
from .rcon import MinecraftRCON
import os

logger = get_logger("minecraft.server")


class MinecraftServer:
    """Manage Minecraft server via systemd with RCON support"""

    def __init__(self):
        self.service_name = os.getenv("MINECRAFT_SERVICE", "minecraft.service")
        self.server_path = Path(os.getenv("MINECRAFT_SERVER_PATH", "/home/minecraft/server"))
        self.server_user = os.getenv("MINECRAFT_USER", "minecraft")
        self.rcon_host = os.getenv("MINECRAFT_RCON_HOST", "127.0.0.1")
        self.rcon_port = int(os.getenv("MINECRAFT_RCON_PORT", "25575"))
        self.rcon_password = os.getenv("MINECRAFT_RCON_PASSWORD", "")
        self.server_type = os.getenv("MINECRAFT_TYPE", "java").lower()  # "java" or "bedrock"
        self.world_path = Path(os.getenv("MINECRAFT_WORLD_PATH", self.server_path / "world"))

        # Cache for process info
        self._last_process_info: Optional[dict] = None

    # ------------------------------------------------------------------
    # systemctl helpers
    # ------------------------------------------------------------------

    async def _systemctl(self, action: str, timeout: int = 30, use_sudo: bool = True) -> Tuple[int, str, str]:
        """Run systemctl command asynchronously"""
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
        """Check if server is active via systemd"""
        code, stdout, _ = await self._systemctl("is-active", timeout=10, use_sudo=False)
        return code == 0 and stdout == "active"

    def _find_process(self) -> Optional[dict]:
        """Find Minecraft Java process via psutil"""
        try:
            for proc in psutil.process_iter(["name", "username", "pid", "create_time",
                                             "cpu_percent", "memory_info"]):
                try:
                    name = proc.info.get("name") or ""
                    user = proc.info.get("username") or ""

                    # Look for java process running as minecraft user
                    if "java" in name.lower() and user == self.server_user:
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

    async def get_status(self) -> Dict:
        """Get comprehensive server status"""
        running = await self.is_running()
        info = {
            "running": running,
            "service": self.service_name,
            "uptime": 0,
            "cpu_percent": 0.0,
            "memory_mb": 0,
            "pid": None,
            "players_online": 0,
            "players_max": 20,
            "type": self.server_type
        }

        if running:
            proc_info = self._find_process()
            if proc_info:
                info["pid"] = proc_info["pid"]
                info["uptime"] = proc_info["uptime"]
                info["cpu_percent"] = proc_info["cpu"]
                info["memory_mb"] = proc_info["mem_mb"]

            # Try to get player count via RCON
            try:
                players_online, players_max = await self.get_player_count()
                info["players_online"] = players_online
                info["players_max"] = players_max
            except Exception as e:
                logger.debug(f"Could not get player count: {e}")

        return info

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
        for _ in range(15):
            await asyncio.sleep(2)
            if await self.is_running():
                logger.info("Minecraft server started")
                return True, "Minecraft Server gestartet!"

        return False, "Start-Befehl gesendet, aber Server nicht aktiv."

    async def stop(self) -> Tuple[bool, str]:
        """Stop server via systemd"""
        if not await self.is_running():
            return False, "Server ist nicht gestartet."

        # Send in-game warning if RCON available
        try:
            await self.rcon_command("say Der Server wird in Kuerze heruntergefahren.")
        except Exception as e:
            logger.debug(f"Could not send in-game warning: {e}")

        code, _, stderr = await self._systemctl("stop", timeout=90)
        if code != 0:
            return False, f"Stop fehlgeschlagen: {stderr}"

        # Wait for shutdown
        for _ in range(30):
            await asyncio.sleep(1)
            if not await self.is_running():
                logger.info("Minecraft server stopped")
                return True, "Minecraft Server gestoppt!"

        return False, "Stop-Befehl gesendet, Server reagiert nicht."

    async def restart(self) -> Tuple[bool, str]:
        """Restart server via systemd"""
        code, _, stderr = await self._systemctl("restart", timeout=120)
        if code != 0:
            return False, f"Restart fehlgeschlagen: {stderr}"

        await asyncio.sleep(5)
        if await self.is_running():
            logger.info("Minecraft server restarted")
            return True, "Minecraft Server neugestartet!"

        return False, "Restart-Befehl gesendet, Server nicht aktiv."

    # ------------------------------------------------------------------
    # RCON Commands
    # ------------------------------------------------------------------

    async def rcon_command(self, command: str) -> str:
        """
        Execute RCON command on server

        Args:
            command: RCON command string

        Returns:
            Command response
        """
        if not self.rcon_password:
            raise RuntimeError("RCON Passwort nicht konfiguriert")

        if not await self.is_running():
            raise RuntimeError("Server ist nicht gestartet")

        try:
            async with MinecraftRCON(
                self.rcon_host,
                self.rcon_port,
                self.rcon_password,
                timeout=5.0
            ) as rcon:
                response = await rcon.command(command)
                logger.debug(f"RCON: {command} -> {response[:100]}")
                return response
        except Exception as e:
            logger.error(f"RCON command failed ({command}): {e}")
            raise

    async def get_players(self) -> List[str]:
        """Get list of online players via RCON"""
        try:
            response = await self.rcon_command("list")
            # Parse response: "There are X of max Y players online: player1, player2"
            if ":" in response:
                players_part = response.split(":")[-1].strip()
                if players_part:
                    return [p.strip() for p in players_part.split(",")]
            return []
        except Exception as e:
            logger.debug(f"Could not get player list: {e}")
            return []

    async def get_player_count(self) -> Tuple[int, int]:
        """
        Get online player count via RCON

        Returns:
            (players_online, players_max)
        """
        try:
            response = await self.rcon_command("list")
            # Parse: "There are X of max Y players online"
            parts = response.lower().split(" of max ")
            if len(parts) >= 2:
                online = int(parts[0].split()[-1])
                max_players = int(parts[1].split()[0])
                return online, max_players
            return 0, 20
        except Exception as e:
            logger.debug(f"Could not parse player count: {e}")
            return 0, 20

    # ------------------------------------------------------------------
    # Server Properties
    # ------------------------------------------------------------------

    def get_properties(self) -> Dict:
        """Parse server.properties file"""
        props_file = self.server_path / "server.properties"
        props = {}

        if not props_file.exists():
            logger.warning(f"server.properties not found: {props_file}")
            return props

        try:
            config = configparser.ConfigParser()
            # Read without sections
            with open(props_file, 'r', encoding='utf-8') as f:
                content = "[default]\n" + f.read()

            config.read_string(content)
            if "default" in config:
                props = dict(config["default"])
        except Exception as e:
            logger.error(f"Error reading server.properties: {e}")

        return props

    def set_property(self, key: str, value: str) -> bool:
        """Set property in server.properties"""
        props_file = self.server_path / "server.properties"

        if not props_file.exists():
            logger.error(f"server.properties not found: {props_file}")
            return False

        try:
            # Read current properties
            config = configparser.ConfigParser()
            with open(props_file, 'r', encoding='utf-8') as f:
                content = "[default]\n" + f.read()

            config.read_string(content)

            # Update property
            if "default" not in config:
                config.add_section("default")
            config.set("default", key, value)

            # Write back (without [default] section header)
            with open(props_file, 'w', encoding='utf-8') as f:
                for prop_key, prop_value in config["default"].items():
                    f.write(f"{prop_key}={prop_value}\n")

            logger.info(f"Set property: {key}={value}")
            return True

        except Exception as e:
            logger.error(f"Error setting property {key}: {e}")
            return False

    # ------------------------------------------------------------------
    # World Management
    # ------------------------------------------------------------------

    @property
    def world_save_path(self) -> Path:
        """Path to world save directory"""
        return self.world_path

    def get_world_size(self) -> int:
        """Get world folder size in bytes"""
        if not self.world_path.exists():
            return 0

        total = 0
        try:
            for entry in self.world_path.rglob("*"):
                if entry.is_file():
                    total += entry.stat().st_size
        except Exception as e:
            logger.debug(f"Error calculating world size: {e}")

        return total
