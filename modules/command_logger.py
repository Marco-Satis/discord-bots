"""
Command Logger
Logs every slash command execution with who, what, when.
Writes to log file, optionally sends to Discord admin channel,
and persists structured data in SQLite (command_log table).
"""

import json
import aiofiles
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
import discord
from modules.database.db_manager import get_db
from utils.logger import get_logger
from utils.config import LOG_DIR
from utils.embeds import COLOR_SUCCESS

logger = get_logger("command_logger")

COMMAND_LOG_FILE = LOG_DIR / "commands.log"


class CommandLogger:
    """
    Logs all slash command executions.

    - File logging: Always active, appends to commands.log
    - Discord logging: Optional, sends to admin log channel
    - SQLite logging: Always active, inserts into command_log table
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

        # SQLite logging
        await self._log_to_sqlite(entry)

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

        except OSError as e:
            logger.error(f"Command log file write failed: {e}")

    async def _log_to_sqlite(self, entry: Dict[str, Any]) -> None:  # type: ignore
        """Persist log entry to SQLite command_log table"""
        try:
            db = await get_db()
            await db.execute(
                "INSERT INTO command_log "
                "(command_name, user_id, user_name, guild_id, channel_id, success, error_message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["command"],
                    str(entry["user_id"]),
                    entry["user_name"],
                    str(entry["guild_id"]) if entry["guild_id"] else None,
                    str(entry["channel_id"]) if entry["channel_id"] else None,
                    entry["success"],
                    entry.get("error"),
                ),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Command log SQLite write failed: {e}")

    async def _log_to_discord(self, entry: Dict[str, Any]) -> None:  # type: ignore
        """Send log entry to Discord admin channel"""
        try:
            # Only log significant commands to Discord (not status/help/ping)
            skip_commands = {"help", "server", "ping", "sat_status", "sat status"}
            if entry['command'] in skip_commands:
                return

            color = COLOR_SUCCESS if entry['success'] else 0xe74c3c

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

        except OSError as e:
            logger.error(f"Failed to read command log: {e}")
            return []

    async def get_command_stats(self, days: int = 7) -> Dict[str, Any]:
        """
        Query SQLite for command usage statistics over the given period.

        Args:
            days: Number of days to look back (default: 7)

        Returns:
            Dict with keys:
                - total: Total command executions
                - unique_users: Number of distinct users
                - success_rate: Percentage of successful commands
                - top_commands: List of (command_name, count) tuples, top 15
                - top_users: List of (user_name, count) tuples, top 10
                - failures: Total failed command executions
                - per_day: List of (date, count) tuples for the period
        """
        stats: Dict[str, Any] = {
            "total": 0,
            "unique_users": 0,
            "success_rate": 0.0,
            "top_commands": [],
            "top_users": [],
            "failures": 0,
            "per_day": [],
        }

        try:
            db = await get_db()

            # Total executions
            cursor = await db.execute(
                "SELECT COUNT(*) FROM command_log "
                "WHERE timestamp >= datetime('now', ?)",
                (f"-{days} days",),
            )
            row = await cursor.fetchone()
            stats["total"] = row[0] if row else 0

            if stats["total"] == 0:
                return stats

            # Unique users
            cursor = await db.execute(
                "SELECT COUNT(DISTINCT user_id) FROM command_log "
                "WHERE timestamp >= datetime('now', ?)",
                (f"-{days} days",),
            )
            row = await cursor.fetchone()
            stats["unique_users"] = row[0] if row else 0

            # Failures
            cursor = await db.execute(
                "SELECT COUNT(*) FROM command_log "
                "WHERE timestamp >= datetime('now', ?) AND success = FALSE",
                (f"-{days} days",),
            )
            row = await cursor.fetchone()
            stats["failures"] = row[0] if row else 0

            # Success rate
            if stats["total"] > 0:
                stats["success_rate"] = round(
                    ((stats["total"] - stats["failures"]) / stats["total"]) * 100, 1
                )

            # Top commands
            cursor = await db.execute(
                "SELECT command_name, COUNT(*) as cnt FROM command_log "
                "WHERE timestamp >= datetime('now', ?) "
                "GROUP BY command_name ORDER BY cnt DESC LIMIT 15",
                (f"-{days} days",),
            )
            stats["top_commands"] = [
                (row[0], row[1]) for row in await cursor.fetchall()
            ]

            # Top users
            cursor = await db.execute(
                "SELECT user_name, COUNT(*) as cnt FROM command_log "
                "WHERE timestamp >= datetime('now', ?) "
                "GROUP BY user_id ORDER BY cnt DESC LIMIT 10",
                (f"-{days} days",),
            )
            stats["top_users"] = [
                (row[0], row[1]) for row in await cursor.fetchall()
            ]

            # Per-day breakdown
            cursor = await db.execute(
                "SELECT DATE(timestamp) as day, COUNT(*) as cnt FROM command_log "
                "WHERE timestamp >= datetime('now', ?) "
                "GROUP BY day ORDER BY day",
                (f"-{days} days",),
            )
            stats["per_day"] = [
                (row[0], row[1]) for row in await cursor.fetchall()
            ]

        except Exception as e:
            logger.error(f"Failed to query command stats: {e}")

        return stats
