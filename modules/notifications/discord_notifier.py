"""
Discord Notifier Module - Centralized Notification System
Handles all Discord notifications (admin alerts, player events, etc.)
"""

import os

import discord
from datetime import datetime
from typing import Optional, List, Tuple
from enum import Enum

from utils.logger import get_logger

logger = get_logger("discord_notifier")


class NotifyLevel(Enum):
    INFO = ("info", 0x5865F2, "ℹ️")
    SUCCESS = ("success", 0x2ecc71, "✅")
    WARNING = ("warning", 0xf39c12, "⚠️")
    ERROR = ("error", 0xe74c3c, "❌")
    CRITICAL = ("critical", 0xe74c3c, "🚨")


class DiscordNotifier:
    """
    Centralized Discord notification system.

    Usage:
        notifier = DiscordNotifier(bot)
        notifier.set_channels(admin_log=123, game_chat=456)
        await notifier.notify_crash(crash_event)
        await notifier.notify_player_join("PlayerName")
    """

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self._admin_channel_id: Optional[int] = None
        self._notify_role_id: Optional[int] = None

    def set_channels(self, admin_log: Optional[int] = None,
                     notify_role: Optional[int] = None) -> None:
        """Configure notification channels"""
        if admin_log:
            self._admin_channel_id = admin_log
        if notify_role:
            self._notify_role_id = notify_role

    def _get_channel(self, channel_id: Optional[int]) -> Optional[discord.TextChannel]:  # type: ignore
        if not channel_id:
            return None
        return self.bot.get_channel(channel_id)

    @property
    def admin_channel(self) -> Optional[discord.TextChannel]:  # type: ignore
        return self._get_channel(self._admin_channel_id)

    @property
    def role_ping(self) -> str:  # type: ignore
        return f"<@&{self._notify_role_id}>" if self._notify_role_id else ""

    # ------------------------------------------------------------------
    # Generic notification
    # ------------------------------------------------------------------

    async def send(self, channel_id: int, title: str, description: str,
                   level: NotifyLevel = NotifyLevel.INFO,
                   ping_role: bool = False, fields: Optional[List[Tuple[str, str, bool]]] = None) -> None:  # type: ignore
        """Send a generic notification embed"""
        channel = self._get_channel(channel_id)
        if not channel:
            return

        _, color, emoji = level.value
        embed = discord.Embed(
            title=f"{emoji} {title}",
            description=description,
            color=color,
            timestamp=datetime.now(),
        )

        if fields:
            for name, value, inline in fields:
                embed.add_field(name=name, value=value, inline=inline)

        content = self.role_ping if ping_role else None
        try:
            await channel.send(content=content, embed=embed)
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    async def send_admin(self, title: str, description: str,
                         level: NotifyLevel = NotifyLevel.INFO,
                         ping_role: bool = False,
                         fields: Optional[List[Tuple[str, str, bool]]] = None) -> None:  # type: ignore
        """Send notification to admin log channel"""
        if self._admin_channel_id:
            await self.send(self._admin_channel_id, title, description,
                            level, ping_role, fields=fields)

    # ------------------------------------------------------------------
    # Server events
    # ------------------------------------------------------------------

    async def notify_server_online(self) -> None:  # type: ignore
        """Server came online"""
        await self.send_admin(
            "Server Online",
            "Satisfactory Server ist online!",
            NotifyLevel.SUCCESS,
        )

    async def notify_server_offline(self) -> None:  # type: ignore
        """Server went offline (graceful)"""
        await self.send_admin(
            "Server Offline",
            "Satisfactory Server wurde gestoppt.",
            NotifyLevel.WARNING,
        )

    async def notify_crash(self, crash_number: int, auto_restart: bool = True) -> None:  # type: ignore
        """Server crash detected"""
        desc = (
            f"Satisfactory ist abgestuerzt!\n"
            f"Crash #{crash_number}"
        )
        if auto_restart:
            desc += "\nAuto-Restart in 30 Sekunden..."

        await self.send_admin(
            "SERVER CRASH ERKANNT",
            desc,
            NotifyLevel.CRITICAL,
            ping_role=True,
        )

    async def notify_restart_success(self, crash_number: int) -> None:  # type: ignore
        """Auto-restart succeeded"""
        await self.send_admin(
            "Auto-Restart erfolgreich",
            f"Server nach Crash #{crash_number} erfolgreich neu gestartet.",
            NotifyLevel.SUCCESS,
        )

    async def notify_restart_failed(self, crash_number: int, reason: str) -> None:  # type: ignore
        """Auto-restart failed"""
        await self.send_admin(
            "Auto-Restart FEHLGESCHLAGEN",
            f"Crash #{crash_number}: {reason}\n\nManuelles Eingreifen erforderlich!",
            NotifyLevel.CRITICAL,
            ping_role=True,
        )

    # ------------------------------------------------------------------
    # Player events
    # ------------------------------------------------------------------

    async def notify_player_join(self, player_name: str) -> None:  # type: ignore
        """Player joined the server"""
        await self.send_admin(
            "Spieler beigetreten",
            f"**{player_name}** hat den Server betreten",
            NotifyLevel.INFO,
        )

    async def notify_player_leave(self, player_name: str, duration_min: int) -> None:  # type: ignore
        """Player left the server"""
        if duration_min > 0:
            hours = duration_min // 60
            mins = duration_min % 60
            time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
            desc = f"**{player_name}** hat den Server verlassen (Spielzeit: {time_str})"
        else:
            desc = f"**{player_name}** hat den Server verlassen"

        await self.send_admin(
            "Spieler verlassen",
            desc,
            NotifyLevel.INFO,
        )

    # ------------------------------------------------------------------
    # Performance events
    # ------------------------------------------------------------------

    async def notify_performance_warning(self, warnings: List[str]) -> None:  # type: ignore
        """Performance threshold exceeded"""
        await self.send_admin(
            "Performance-Warnung",
            "\n".join(warnings),
            NotifyLevel.WARNING,
        )

    # ------------------------------------------------------------------
    # Backup events
    # ------------------------------------------------------------------

    async def notify_backup_success(self, filename: str, size_mb: float,
                                     backup_type: str = "Auto") -> None:  # type: ignore
        """Backup completed successfully"""
        await self.send_admin(
            f"{backup_type}-Backup erstellt",
            f"**Datei:** {filename}\n**Groesse:** {size_mb:.1f} MB",
            NotifyLevel.SUCCESS,
        )

    async def notify_backup_failed(self, reason: str, backup_type: str = "Auto") -> None:  # type: ignore
        """Backup failed"""
        await self.send_admin(
            f"{backup_type}-Backup FEHLGESCHLAGEN",
            reason,
            NotifyLevel.ERROR,
            ping_role=True,
        )

    # ------------------------------------------------------------------
    # Update events
    # ------------------------------------------------------------------

    async def notify_update_available(self, installed_build: str, available_build: str,
                                      extra_text: str = "") -> None:  # type: ignore
        """Server update available"""
        desc = (
            f"**Installiert:** Build {installed_build}\n"
            f"**Verfuegbar:** Build {available_build}\n\n"
            f"Verwende `/sat update` um das Update durchzufuehren."
        )
        if extra_text:
            desc += extra_text
        await self.send_admin(
            "Server Update verfuegbar",
            desc,
            NotifyLevel.INFO,
        )

    async def notify_player_update(
        self, old_build: str, new_build: str, channel_id: Optional[int] = None
    ) -> None:
        """Benachrichtigt Spieler ueber erfolgreiches Server-Update.

        Postet im Spieler-Channel (oder Admin-Channel als Fallback).
        """
        description = (
            f"Der Satisfactory Server wurde aktualisiert und ist wieder online.\n\n"
            f"**Vorherige Version:** Build {old_build}\n"
            f"**Neue Version:** Build {new_build}"
        )
        if channel_id:
            await self.send(
                channel_id,
                "Server-Update installiert",
                description,
                NotifyLevel.SUCCESS,
            )
        else:
            await self.send_admin(
                "Server-Update installiert",
                description,
                NotifyLevel.SUCCESS,
            )

    async def notify_update_rollback(
        self, version: str, reason: str
    ) -> None:
        """Benachrichtigt Admins ueber fehlgeschlagenes Update mit Rollback."""
        await self.send_admin(
            "Auto-Update FEHLGESCHLAGEN — Rollback ausgefuehrt",
            (
                f"Das Update auf Version {version} ist fehlgeschlagen.\n"
                f"**Grund:** {reason}\n\n"
                f"Das Pre-Update-Backup wurde wiederhergestellt und der Server neu gestartet."
            ),
            NotifyLevel.ERROR,
            ping_role=True,
        )

    # ------------------------------------------------------------------
    # DM an Owner (I5)
    # ------------------------------------------------------------------

    async def send_dm_to_owner(
        self,
        title: str,
        description: str,
        level: NotifyLevel = NotifyLevel.CRITICAL,
        fields: Optional[List[Tuple[str, str, bool]]] = None,
    ) -> None:
        """Sendet eine DM an den Bot-Owner (fuer kritische Fehler)."""
        owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
        if not owner_id:
            logger.warning("BOT_OWNER_ID nicht konfiguriert — DM nicht gesendet")
            return

        try:
            user = await self.bot.fetch_user(owner_id)
            _, color, emoji = level.value
            embed = discord.Embed(
                title=f"{emoji} {title}",
                description=description,
                color=color,
                timestamp=datetime.now(),
            )
            if fields:
                for name, value, inline in fields:
                    embed.add_field(name=name, value=value, inline=inline)
            await user.send(embed=embed)
        except discord.Forbidden:
            logger.warning("Owner hat DMs deaktiviert — Benachrichtigung nur via Channel")
        except Exception as e:
            logger.error(f"DM an Owner fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Scheduled events
    # ------------------------------------------------------------------

    async def notify_scheduled_restart(self, time_str: str) -> None:  # type: ignore
        """Scheduled restart announcement"""
        await self.send_admin(
            "Geplanter Restart",
            f"Taeglicher Restart um {time_str} ausgefuehrt.",
            NotifyLevel.INFO,
        )

    async def notify_daily_report(self, report_text: str) -> None:  # type: ignore
        """Send daily status report"""
        channel = self.admin_channel
        if not channel:
            return

        embed = discord.Embed(
            title="📋 Täglicher Status-Report",
            description=report_text,
            color=0x5865F2,
            timestamp=datetime.now(),
        )
        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send daily report: {e}")

    async def notify_weekly_report(self, report_text: str) -> None:  # type: ignore
        """Send weekly player report"""
        channel = self.admin_channel
        if not channel:
            return

        embed = discord.Embed(
            title="📊 Wochenbericht",
            description=report_text,
            color=0x5865F2,
            timestamp=datetime.now(),
        )
        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send weekly report: {e}")
