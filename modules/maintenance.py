"""
Maintenance Module — Phase 16
Bot self-update, changelog, network monitoring, token rotation reminder.
"""

import json
import asyncio
import subprocess
import socket
import aiohttp
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any
from datetime import datetime, timedelta

from utils import get_logger, PROJECT_ROOT, DATA_DIR

logger = get_logger("modules.maintenance")


class BotMaintenance:
    """Bot maintenance utilities including self-update, changelog, network monitoring"""

    def __init__(self) -> None:
        """Initialize BotMaintenance"""
        self.changelog_file = DATA_DIR / "changelog.json"
        self.version_file = PROJECT_ROOT / "VERSION"
        self.token_log_file = DATA_DIR / "token_rotation.json"
        self._changelog: List[Dict[str, Any]] = []
        self._token_log: Dict[str, Any] = {}
        self._load_changelog()
        self._load_token_log()

    def _load_changelog(self) -> None:  # type: ignore
        """Load changelog from JSON file"""
        try:
            if self.changelog_file.exists():
                with open(self.changelog_file, "r", encoding="utf-8") as f:
                    self._changelog = json.load(f)
                logger.info(f"Loaded changelog with {len(self._changelog)} entries")
            else:
                self._changelog = []
        except Exception as e:
            logger.error(f"Failed to load changelog: {e}")
            self._changelog = []

    def _save_changelog(self) -> None:  # type: ignore
        """Save changelog to JSON file"""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.changelog_file, "w", encoding="utf-8") as f:
                json.dump(self._changelog, f, indent=2, ensure_ascii=False)
            logger.info("Saved changelog")
        except Exception as e:
            logger.error(f"Failed to save changelog: {e}")

    def _load_token_log(self) -> None:  # type: ignore
        """Load token rotation log"""
        try:
            if self.token_log_file.exists():
                with open(self.token_log_file, "r", encoding="utf-8") as f:
                    self._token_log = json.load(f)
                logger.info("Loaded token rotation log")
            else:
                self._token_log = {}
        except Exception as e:
            logger.error(f"Failed to load token log: {e}")
            self._token_log = {}

    def _save_token_log(self) -> None:  # type: ignore
        """Save token rotation log"""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.token_log_file, "w", encoding="utf-8") as f:
                json.dump(self._token_log, f, indent=2, ensure_ascii=False)
            logger.info("Saved token rotation log")
        except Exception as e:
            logger.error(f"Failed to save token log: {e}")

    # ====================================================================
    # 16a: Bot self-update via git pull
    # ====================================================================

    async def self_update(self) -> Tuple[bool, str]:
        """
        Pull latest changes from git repo and restart bots

        Returns:
            (success, message)
        """
        try:
            logger.info("Starting self-update via git pull")

            # Check if we're in a git repo
            git_dir = PROJECT_ROOT / ".git"
            if not git_dir.exists():
                return False, "Kein Git-Repository gefunden im Projekt-Verzeichnis"

            # Run git pull
            result = await self._run_command(
                ["git", "-C", str(PROJECT_ROOT), "pull", "origin", "main"],
                timeout=30
            )

            if result["returncode"] != 0:
                error_msg = result["stderr"].decode("utf-8", errors="ignore")
                logger.error(f"Git pull failed: {error_msg}")
                return False, f"Git-Fehler: {error_msg[:200]}"

            output = result["stdout"].decode("utf-8", errors="ignore")
            logger.info(f"Git pull output: {output}")

            # Update was successful
            return True, f"✓ Repository aktualisiert:\n{output[:300]}"

        except Exception as e:
            logger.error(f"Self-update failed: {e}")
            return False, f"Fehler beim Update: {str(e)}"

    async def check_for_updates(self) -> Optional[str]:
        """
        Check if remote has new commits

        Returns:
            Commit message if updates available, None otherwise
        """
        try:
            git_dir = PROJECT_ROOT / ".git"
            if not git_dir.exists():
                return None

            # Fetch latest from remote
            result = await self._run_command(
                ["git", "-C", str(PROJECT_ROOT), "fetch", "origin", "main"],
                timeout=15
            )

            if result["returncode"] != 0:
                logger.warning("Failed to fetch from remote")
                return None

            # Compare local vs remote
            result = await self._run_command(
                ["git", "-C", str(PROJECT_ROOT), "rev-list", "--count", "HEAD..origin/main"],
                timeout=10
            )

            if result["returncode"] == 0:
                behind = int(result["stdout"].decode("utf-8", errors="ignore").strip() or "0")
                if behind > 0:
                    # Get latest commit message
                    result = await self._run_command(
                        ["git", "-C", str(PROJECT_ROOT), "log", "-1", "--pretty=%B", "origin/main"],
                        timeout=10
                    )
                    if result["returncode"] == 0:
                        msg = result["stdout"].decode("utf-8", errors="ignore").strip()
                        logger.info(f"Updates available: {behind} commits behind")
                        return f"{behind} neue Commits verfuegbar:\n{msg[:200]}"

            return None

        except Exception as e:
            logger.warning(f"Failed to check for updates: {e}")
            return None

    # ====================================================================
    # 16b: Changelog system
    # ====================================================================

    def add_changelog_entry(self, version: str, changes: List[str], author: str) -> bool:  # type: ignore
        """
        Add a changelog entry

        Args:
            version: Version number (e.g., "1.0.0")
            changes: List of change descriptions
            author: Author name or Discord username

        Returns:
            True if successful
        """
        try:
            entry = {
                "version": version,
                "date": datetime.now().isoformat(),
                "changes": changes,
                "author": author,
            }

            # Check if version already exists
            if any(e.get("version") == version for e in self._changelog):
                logger.warning(f"Version {version} already exists in changelog")
                return False

            # Add at the beginning (newest first)
            self._changelog.insert(0, entry)
            self._save_changelog()
            logger.info(f"Added changelog entry: {version} by {author}")
            return True

        except Exception as e:
            logger.error(f"Failed to add changelog entry: {e}")
            return False

    def get_changelog(self, limit: int = 10) -> List[Dict[str, Any]]:  # type: ignore
        """
        Get changelog entries

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of changelog entries (newest first)
        """
        return self._changelog[:limit]

    def get_current_version(self) -> str:  # type: ignore
        """
        Get current bot version

        Returns:
            Version string (e.g., "1.0.0")
        """
        try:
            if self.version_file.exists():
                with open(self.version_file, "r", encoding="utf-8") as f:
                    return f.read().strip()
        except Exception as e:
            logger.warning(f"Failed to read version: {e}")

        return "1.0.0"

    def set_version(self, version: str) -> bool:  # type: ignore
        """
        Set bot version

        Args:
            version: Version string

        Returns:
            True if successful
        """
        try:
            PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
            with open(self.version_file, "w", encoding="utf-8") as f:
                f.write(version)
            logger.info(f"Set version to {version}")
            return True
        except Exception as e:
            logger.error(f"Failed to set version: {e}")
            return False

    # ====================================================================
    # 16c: Network monitoring
    # ====================================================================

    async def check_network(self) -> Dict[str, Any]:  # type: ignore
        """
        Check network connectivity and latency

        Returns:
            Dictionary with network status information
        """
        results = {
            "timestamp": datetime.now().isoformat(),
            "gateway": None,
            "dns": None,
            "discord_api": None,
            "steam_api": None,
            "internet": None,
        }

        try:
            # Check internet connectivity
            results["internet"] = await self._ping_host("8.8.8.8")

            # Check DNS
            results["dns"] = await self._check_dns()

            # Check Discord API
            results["discord_api"] = await self._ping_discord_api()

            # Check Steam API
            results["steam_api"] = await self._ping_steam_api()

            logger.info(f"Network check completed: {results}")

        except Exception as e:
            logger.error(f"Network check failed: {e}")

        return results

    async def _ping_host(self, host: str, timeout: int = 3) -> Optional[int]:  # type: ignore
        """
        Ping a host and return latency in ms

        Args:
            host: IP address or hostname
            timeout: Timeout in seconds

        Returns:
            Latency in ms or None if unreachable
        """
        try:
            result = await self._run_command(
                ["ping", "-c", "1", "-W", str(timeout * 1000), host],
                timeout=timeout + 1
            )

            if result["returncode"] == 0:
                # Parse ping output to get latency
                output = result["stdout"].decode("utf-8", errors="ignore")
                if "time=" in output:
                    time_str = output.split("time=")[1].split()[0]
                    return int(float(time_str))

        except Exception as e:
            logger.debug(f"Ping to {host} failed: {e}")

        return None

    async def _check_dns(self) -> bool:  # type: ignore
        """
        Check DNS resolution

        Returns:
            True if DNS is working
        """
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                socket.gethostbyname,
                "discord.com"
            )
            return result is not None
        except Exception as e:
            logger.debug(f"DNS check failed: {e}")
            return False

    async def _ping_discord_api(self) -> Optional[int]:  # type: ignore
        """
        Check Discord API connectivity

        Returns:
            Latency in ms or None if unreachable
        """
        try:
            start = datetime.now()
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://discordapp.com/api/v10/gateway",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        elapsed = (datetime.now() - start).total_seconds() * 1000
                        return int(elapsed)
        except Exception as e:
            logger.debug(f"Discord API check failed: {e}")

        return None

    async def _ping_steam_api(self) -> Optional[int]:  # type: ignore
        """
        Check Steam API connectivity

        Returns:
            Latency in ms or None if unreachable
        """
        try:
            start = datetime.now()
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.steampowered.com/ISteamApps/GetAppList/v2/",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        elapsed = (datetime.now() - start).total_seconds() * 1000
                        return int(elapsed)
        except Exception as e:
            logger.debug(f"Steam API check failed: {e}")

        return None

    async def check_ports(self) -> Dict[str, Any]:  # type: ignore
        """
        Check if game server ports are accessible

        Returns:
            Dictionary with port status information
        """
        results = {
            "timestamp": datetime.now().isoformat(),
            "satisfactory": None,
            "minecraft": None,
        }

        try:
            # Check Satisfactory port (default 15777)
            results["satisfactory"] = await self._check_port("localhost", 15777, timeout=2)

            # Check Minecraft port (default 25565)
            results["minecraft"] = await self._check_port("localhost", 25565, timeout=2)

            logger.info(f"Port check completed: {results}")

        except Exception as e:
            logger.error(f"Port check failed: {e}")

        return results

    async def _check_port(self, host: str, port: int, timeout: int = 3) -> Dict[str, Any]:  # type: ignore
        """
        Check if a port is open

        Args:
            host: Hostname or IP
            port: Port number
            timeout: Timeout in seconds

        Returns:
            Dictionary with port status
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            return {"open": True, "latency_ms": None}

        except asyncio.TimeoutError:
            return {"open": False, "reason": "timeout"}
        except ConnectionRefusedError:
            return {"open": False, "reason": "refused"}
        except OSError:
            return {"open": False, "reason": "connection_failed"}
        except Exception as e:
            return {"open": False, "reason": str(e)[:50]}

    # ====================================================================
    # 16d: Token rotation reminder
    # ====================================================================

    def check_token_age(self) -> Dict[str, Any]:  # type: ignore
        """
        Check if tokens should be rotated (>90 days)

        Returns:
            Dictionary with token rotation status
        """
        results = {
            "timestamp": datetime.now().isoformat(),
            "tokens_need_rotation": [],
        }

        try:
            today = datetime.now()
            threshold = today - timedelta(days=90)

            for token_name, token_data in self._token_log.items():
                if isinstance(token_data, dict):
                    rotated_at = token_data.get("rotated_at")
                else:
                    rotated_at = token_data

                try:
                    rotated_date = datetime.fromisoformat(rotated_at)
                    if rotated_date < threshold:
                        days_old = (today - rotated_date).days
                        results["tokens_need_rotation"].append({
                            "name": token_name,
                            "rotated_at": rotated_at,
                            "days_old": days_old,
                        })
                except (ValueError, TypeError):
                    logger.warning(f"Invalid date for token {token_name}: {rotated_at}")

            if results["tokens_need_rotation"]:
                logger.warning(f"Tokens need rotation: {len(results['tokens_need_rotation'])}")
            else:
                logger.info("All tokens are current")

        except Exception as e:
            logger.error(f"Failed to check token age: {e}")

        return results

    def record_token_rotation(self, token_name: str) -> bool:  # type: ignore
        """
        Record a token rotation

        Args:
            token_name: Name of the token (e.g., "discord_bot", "steam_api")

        Returns:
            True if successful
        """
        try:
            self._token_log[token_name] = {
                "rotated_at": datetime.now().isoformat(),
                "rotated_by": "system",
            }
            self._save_token_log()
            logger.info(f"Recorded token rotation: {token_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to record token rotation: {e}")
            return False

    # ====================================================================
    # Helper methods
    # ====================================================================

    async def _run_command(
        self,
        cmd: List[str],
        timeout: int = 30
    ) -> Dict[str, Any]:  # type: ignore
        """
        Run a shell command asynchronously

        Args:
            cmd: Command as list of strings
            timeout: Timeout in seconds

        Returns:
            Dictionary with returncode, stdout, stderr
        """
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
                return {
                    "returncode": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            except asyncio.TimeoutError:
                process.kill()
                return {
                    "returncode": -1,
                    "stdout": b"",
                    "stderr": b"Timeout",
                }

        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return {
                "returncode": -1,
                "stdout": b"",
                "stderr": str(e).encode(),
            }
