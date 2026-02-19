"""
Command Logger
Logs every slash command execution with who, what, when.
Writes to log file and optionally sends to Discord admin channel.
"""

import json
import aiofiles
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
import discord
from utils.logger import get_logger
from utils.config import LOG_DIR

logger = get_logger("command_logger")

COMMAND_LOG_FILE = LOG_DIR / "commands.log"


class CommandLogger:
    """
    Logs all slash command executions.
    
    - File logging: Always active, appends to commands.log
    - Discord logging: Optional, sends to admin log channel
    """

    def __init__(self, admin_channel: Optional[discord.TextChannel] = None) -> None:
        self.admin_channel = admin_channel
        self.enabled = True
        self._ensure_log_dir()

    def _ensure_log_dir(self) -> None:  # type: ignore
        """Ensure log directory exists"""
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    def set_admin_channel(self, channel: discord.TextChannel) -> None:  # type: ignore
        """Set the admin log channel"""
        self.admin_channel = channel

    async def log_command(
        self,
        interaction: discord.Interaction,
        command_name: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:  # type: ignore
        """
        Log a command execution.
        
        Args:
            interaction: The Discord interaction
            command_name: Override command name (auto-detected if None)
            params: Command parameters dict
            success: Whether command succeeded
            error: Error message if failed
        """
        if not self.enabled:
            return

        # Auto-detect command name
        if not command_name and interaction.command:
            command_name = interaction.command.qualified_name

        user = interaction.user
        guild = interaction.guild
        channel = interaction.channel

        entry = {
            "timestamp": datetime.now().isoformat(),
            "command": command_name or "unknown",
            "user_id": user.id,
            "user_name": str(user),
            "user_display": user.display_name,
            "guild_id": guild.id if guild else None,
            "guild_name": guild.name if guild else None,
            "channel_id": channel.id if channel else None,
            "channel_name": getattr(channel, "name", "DM"),
            "params": params or {},
            "success": success,
            "error": error,
        }

        # File logging
        await self._log_to_file(entry)

        # Discord logging (only for important commands)
        if self.admin_channel:
            await self._log_to_discord(entry)

    async def _log_to_file(self, entry: Dict[str, Any]) -> None:  # type: ignore
        """Append log entry to file"""
        try:
            line = (
                f"[{entry['timestamp'][:19]}] "
                f"{entry['user_display']} ({entry['user_id']}) "
                f"/{entry['command']} "
            )
            if entry['params']:
                params_str = " ".join(
                    f"{k}={v}" for k, v in entry['params'].items()
                )
                line += f"[{params_str}] "
            if not entry['success']:
                line += f"FAILED: {entry.get('error', 'unknown')} "

            line += f"#{entry['channel_name']}\n"

            async with aiofiles.open(COMMAND_LOG_FILE, "a", encoding="utf-8") as f:
                await f.write(line)

        except (IOError, OSError) as e:
            logger.error(f"Command log file write failed: {e}")

    async def _log_to_discord(self, entry: Dict[str, Any]) -> None:  # type: ignore
        """Send log entry to Discord admin channel"""
        try:
            # Only log significant commands to Discord (not status/help/ping)
            skip_commands = {"help", "server", "ping", "sat_status", "sat status"}
            if entry['command'] in skip_commands:
                return

            color = 0x2ecc71 if entry['success'] else 0xe74c3c

            embed = discord.Embed(
                color=color,
                timestamp=datetime.fromisoformat(entry['timestamp'])
            )

            cmd_text = f"/{entry['command']}"
            if entry['params']:
                params_str = " ".join(
                    f"`{k}`=`{v}`" for k, v in entry['params'].items()
                )
                cmd_text += f" {params_str}"

            embed.add_field(name="Command", value=cmd_text, inline=False)
            embed.add_field(
                name="User",
                value=f"{entry['user_display']} ({entry['user_id']})",
                inline=True
            )
            embed.add_field(
                name="Channel",
                value=f"#{entry['channel_name']}",
                inline=True
            )

            if not entry['success']:
                embed.add_field(
                    name="Error",
                    value=entry.get('error', 'Unknown')[:200],
                    inline=False
                )

            await self.admin_channel.send(embed=embed)

        except Exception as e:
            logger.debug(f"Discord command log failed: {e}")

    async def get_recent(self, count: int = 50) -> List[str]:  # type: ignore
        """Get recent command log entries from file"""
        try:
            if not COMMAND_LOG_FILE.exists():
                return []

            async with aiofiles.open(COMMAND_LOG_FILE, "r", encoding="utf-8") as f:
                lines = await f.readlines()

            return lines[-count:]

        except (IOError, OSError) as e:
            logger.error(f"Failed to read command log: {e}")
            return []
