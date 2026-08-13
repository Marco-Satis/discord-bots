"""
F59: Command-Nutzungsstatistiken — Tracking und Auswertung von Slash Commands

Zeichnet jede Slash-Command-Ausfuehrung in der SQLite-Tabelle command_log auf
und bietet Admin-Commands zur Auswertung der Nutzungsstatistiken.

Listener:
  on_app_command_completion — Loggt jede erfolgreiche Command-Ausfuehrung

Commands (nur Admin):
  /cmdstats top [tage]     — Top 10 meistgenutzte Commands der letzten N Tage
  /cmdstats user <user>    — Command-Nutzung eines bestimmten Users
  /cmdstats unused         — Commands die in den letzten 30 Tagen nicht genutzt wurden
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.database.db_manager import get_db
from utils.logger import get_logger
from utils.embeds import COLOR_INFO, COLOR_SUCCESS, COLOR_WARNING

logger = get_logger("cogs.command_stats")


class CommandStatsCog(commands.Cog):
    """Command-Nutzungsstatistiken — Tracking und Auswertung"""

    # ==================================================================
    # Command-Gruppe
    # ==================================================================

    cmdstats_grp = app_commands.Group(
        name="cmdstats",
        description="Command-Nutzungsstatistiken anzeigen",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        logger.info("CommandStatsCog initialisiert")

    # ------------------------------------------------------------------
    # Cog Lifecycle
    # ------------------------------------------------------------------

    async def cog_load(self) -> None:
        """Sicherstellen dass die command_log-Tabelle existiert"""
        await self._ensure_table()
        logger.info("CommandStatsCog geladen")

    async def _ensure_table(self) -> None:
        """Erstellt die command_log-Tabelle falls nicht vorhanden (Fallback)"""
        db = await get_db()
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS command_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                command_name TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_name TEXT,
                guild_id TEXT,
                channel_id TEXT,
                success BOOLEAN DEFAULT TRUE,
                error_message TEXT
            )
            """
        )
        # Indices für schnelle Abfragen
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_cmdlog_cmd "
            "ON command_log(command_name, timestamp)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_cmdlog_user "
            "ON command_log(user_id)"
        )
        await db.commit()

    # ------------------------------------------------------------------
    # Event Listener — Loggt jede Command-Ausfuehrung
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command,
    ) -> None:
        """Zeichnet jede erfolgreiche Slash-Command-Ausfuehrung auf"""
        try:
            command_name = command.qualified_name if command else "unknown"
            user = interaction.user
            guild = interaction.guild

            db = await get_db()
            await db.execute(
                """
                INSERT INTO command_log
                    (timestamp, command_name, user_id, user_name, guild_id, success)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (
                    datetime.now().isoformat(),
                    command_name,
                    str(user.id),
                    str(user),
                    str(guild.id) if guild else None,
                ),
            )
            await db.commit()
        except Exception as e:
            # Logging-Fehler sollen den Bot nicht stoeren
            logger.debug(f"Command-Log Fehler: {e}")

    # ------------------------------------------------------------------
    # /cmdstats top [tage]
    # ------------------------------------------------------------------

    @cmdstats_grp.command(
        name="top",
        description="Top 10 meistgenutzte Commands anzeigen",
    )
    @app_commands.describe(
        tage="Zeitraum in Tagen (Standard: 7)"
    )
    async def cmdstats_top(
        self,
        interaction: discord.Interaction,
        tage: int = 7,
    ) -> None:
        """Zeigt die meistgenutzten Commands der letzten N Tage"""
        await interaction.response.defer(ephemeral=True)

        # Validierung
        if tage < 1 or tage > 365:
            await interaction.followup.send(
                "Tage muessen zwischen 1 und 365 liegen.", ephemeral=True
            )
            return

        db = await get_db()

        # Top 10 Commands nach Haeufigkeit
        cursor = await db.execute(
            """
            SELECT command_name, COUNT(*) as cnt
            FROM command_log
            WHERE timestamp >= datetime('now', ?)
            GROUP BY command_name
            ORDER BY cnt DESC
            LIMIT 10
            """,
            (f"-{tage} days",),
        )
        top_commands = await cursor.fetchall()

        # Gesamtstatistiken
        cursor = await db.execute(
            """
            SELECT
                COUNT(*) as total,
                COUNT(DISTINCT user_id) as unique_users,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures
            FROM command_log
            WHERE timestamp >= datetime('now', ?)
            """,
            (f"-{tage} days",),
        )
        totals = await cursor.fetchone()
        total = totals[0] if totals else 0
        unique_users = totals[1] if totals else 0
        failures = totals[2] if totals else 0

        if total == 0:
            await interaction.followup.send(
                f"Keine Command-Ausfuehrungen in den letzten {tage} Tagen.",
                ephemeral=True,
            )
            return

        # Erfolgsrate berechnen
        success_rate = round(((total - failures) / total) * 100, 1) if total > 0 else 0

        # Embed erstellen
        embed = discord.Embed(
            title=f"Top 10 Commands (letzte {tage} Tage)",
            color=COLOR_INFO,
            timestamp=datetime.now(),
        )

        # Rangliste formatieren
        lines = []
        for i, row in enumerate(top_commands, 1):
            name = row[0]
            count = row[1]
            # Prozentanteil am Gesamtvolumen
            pct = round((count / total) * 100, 1)
            lines.append(f"`{i:2d}.` **/{name}** — {count}x ({pct}%)")

        embed.description = "\n".join(lines) if lines else "Keine Daten"

        embed.add_field(
            name="Gesamt",
            value=f"{total} Ausfuehrungen",
            inline=True,
        )
        embed.add_field(
            name="Nutzer",
            value=f"{unique_users} verschiedene",
            inline=True,
        )
        embed.add_field(
            name="Erfolgsrate",
            value=f"{success_rate}%",
            inline=True,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /cmdstats user <user>
    # ------------------------------------------------------------------

    @cmdstats_grp.command(
        name="user",
        description="Command-Nutzung eines bestimmten Users anzeigen",
    )
    @app_commands.describe(
        user="Der User dessen Statistiken angezeigt werden sollen"
    )
    async def cmdstats_user(
        self,
        interaction: discord.Interaction,
        user: discord.User,
    ) -> None:
        """Zeigt die Command-Nutzung eines bestimmten Users"""
        await interaction.response.defer(ephemeral=True)

        db = await get_db()
        user_id_str = str(user.id)

        # Gesamtanzahl Commands des Users
        cursor = await db.execute(
            "SELECT COUNT(*) FROM command_log WHERE user_id = ?",
            (user_id_str,),
        )
        row = await cursor.fetchone()
        total = row[0] if row else 0

        if total == 0:
            await interaction.followup.send(
                f"**{user.display_name}** hat keine Commands ausgefuehrt.",
                ephemeral=True,
            )
            return

        # Top 10 Commands des Users
        cursor = await db.execute(
            """
            SELECT command_name, COUNT(*) as cnt
            FROM command_log
            WHERE user_id = ?
            GROUP BY command_name
            ORDER BY cnt DESC
            LIMIT 10
            """,
            (user_id_str,),
        )
        top_cmds = await cursor.fetchall()

        # Letzter Command
        cursor = await db.execute(
            """
            SELECT command_name, timestamp
            FROM command_log
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (user_id_str,),
        )
        last_cmd = await cursor.fetchone()

        # Erster Command (Mitglied seit)
        cursor = await db.execute(
            """
            SELECT timestamp
            FROM command_log
            WHERE user_id = ?
            ORDER BY timestamp ASC
            LIMIT 1
            """,
            (user_id_str,),
        )
        first_cmd = await cursor.fetchone()

        # Nutzung letzte 7 Tage
        cursor = await db.execute(
            """
            SELECT COUNT(*) FROM command_log
            WHERE user_id = ? AND timestamp >= datetime('now', '-7 days')
            """,
            (user_id_str,),
        )
        recent_row = await cursor.fetchone()
        recent_count = recent_row[0] if recent_row else 0

        # Embed erstellen
        embed = discord.Embed(
            title=f"Command-Statistiken: {user.display_name}",
            color=COLOR_INFO,
            timestamp=datetime.now(),
        )

        if user.avatar:
            embed.set_thumbnail(url=user.avatar.url)

        # Top Commands des Users
        lines = []
        for i, row in enumerate(top_cmds, 1):
            name = row[0]
            count = row[1]
            lines.append(f"`{i:2d}.` **/{name}** — {count}x")

        embed.description = "\n".join(lines) if lines else "Keine Daten"

        embed.add_field(
            name="Gesamt",
            value=f"{total} Commands",
            inline=True,
        )
        embed.add_field(
            name="Letzte 7 Tage",
            value=f"{recent_count} Commands",
            inline=True,
        )

        if last_cmd:
            last_time = last_cmd[1][:16] if last_cmd[1] else "?"
            embed.add_field(
                name="Letzter Command",
                value=f"/{last_cmd[0]} ({last_time})",
                inline=False,
            )

        if first_cmd:
            first_time = first_cmd[0][:10] if first_cmd[0] else "?"
            embed.add_field(
                name="Erster Command",
                value=first_time,
                inline=True,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /cmdstats unused
    # ------------------------------------------------------------------

    @cmdstats_grp.command(
        name="unused",
        description="Commands die in den letzten 30 Tagen nicht genutzt wurden",
    )
    async def cmdstats_unused(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Zeigt registrierte Commands die in 30 Tagen nicht genutzt wurden"""
        await interaction.response.defer(ephemeral=True)

        # Alle registrierten Slash Commands sammeln
        registered_commands: set[str] = set()
        for cmd in self.bot.tree.get_commands():
            self._collect_command_names(cmd, registered_commands)

        if not registered_commands:
            await interaction.followup.send(
                "Keine registrierten Commands gefunden.", ephemeral=True
            )
            return

        # Commands die in den letzten 30 Tagen genutzt wurden
        db = await get_db()
        cursor = await db.execute(
            """
            SELECT DISTINCT command_name
            FROM command_log
            WHERE timestamp >= datetime('now', '-30 days')
            """
        )
        used_commands = {row[0] for row in await cursor.fetchall()}

        # Differenz: registriert aber nicht genutzt
        unused = sorted(registered_commands - used_commands)

        if not unused:
            embed = discord.Embed(
                title="Alle Commands wurden genutzt",
                description=(
                    f"Alle {len(registered_commands)} registrierten Commands "
                    f"wurden in den letzten 30 Tagen mindestens einmal ausgefuehrt."
                ),
                color=COLOR_SUCCESS,
                timestamp=datetime.now(),
            )
        else:
            lines = [f"- `/{name}`" for name in unused]
            description = "\n".join(lines)
            if len(description) > 4000:
                description = description[:4000] + "\n..."

            embed = discord.Embed(
                title=f"Ungenutzte Commands ({len(unused)}/{len(registered_commands)})",
                description=description,
                color=COLOR_WARNING,
                timestamp=datetime.now(),
            )
            embed.set_footer(
                text="Commands die in den letzten 30 Tagen nicht ausgefuehrt wurden"
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _collect_command_names(
        self,
        cmd: app_commands.Command | app_commands.Group,
        result: set[str],
        prefix: str = "",
    ) -> None:
        """Sammelt rekursiv alle Command-Namen (inkl. Subcommands/Gruppen)"""
        full_name = f"{prefix} {cmd.name}".strip() if prefix else cmd.name

        if isinstance(cmd, app_commands.Group):
            # Gruppe: Subcommands rekursiv durchgehen
            for sub in cmd.commands:
                self._collect_command_names(sub, result, prefix=full_name)
        else:
            # Normaler Command
            result.add(full_name)

    # ------------------------------------------------------------------
    # Fehlerbehandlung
    # ------------------------------------------------------------------

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung für diesen Befehl.", ephemeral=True
                )
        else:
            logger.error(
                f"CommandStats Command-Fehler: {error}", exc_info=True
            )
            msg = f"Fehler: {str(error)[:200]}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Cog laden"""
    await bot.add_cog(CommandStatsCog(bot))
