"""
Profile Cog — Spieler-Statistiken und Leaderboard

Stellt Slash-Commands fuer Spieler-Profile und Ranglisten bereit:
  /profil [spieler]              — Detailliertes Spieler-Profil anzeigen
  /spieler_leaderboard [zeitraum] — Top 10 Spieler nach Spielzeit

Datenquelle: SQLite-Tabelle `player_sessions` mit Spalten:
  id, player_name, server_id, join_time, leave_time, duration_minutes, ip_address
"""

from __future__ import annotations

from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone

from modules.database.db_manager import get_db
from utils.logger import get_logger

logger = get_logger("cogs.profile")


# ======================================================================
# Hilfsfunktionen
# ======================================================================

def _format_playtime(minutes: int) -> str:
    """Formatiert Spielzeit in Minuten zu lesbarem Format.

    Args:
        minutes: Spielzeit in Minuten.

    Returns:
        Formatierter String (z.B. '3d 5h 20m').
    """
    if minutes is None or minutes <= 0:
        return "0m"
    if minutes < 60:
        return f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {mins}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h {mins}m"


def _format_datetime(dt_string: Optional[str]) -> str:
    """Formatiert einen Datetime-String fuer die Anzeige.

    Args:
        dt_string: ISO-Datetime-String oder None.

    Returns:
        Formatierter String oder 'Unbekannt'.
    """
    if not dt_string:
        return "Unbekannt"
    try:
        dt = datetime.fromisoformat(dt_string)
        return dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return dt_string


class ProfileCog(commands.Cog):
    """Spieler-Statistiken und Leaderboard fuer Game-Server.

    Zeigt detaillierte Spieler-Profile mit Spielzeit, Sessions,
    Lieblingsserver und Rang im Leaderboard an.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ==================================================================
    # Autocomplete: Spielername
    # ==================================================================

    async def _player_name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete fuer Spielernamen aus der Datenbank.

        Liefert bis zu 25 passende Spielernamen basierend auf der
        aktuellen Eingabe des Nutzers.
        """
        try:
            db = await get_db()
            if current:
                cursor = await db.execute(
                    "SELECT DISTINCT player_name FROM player_sessions "
                    "WHERE player_name LIKE ? ORDER BY player_name LIMIT 25",
                    (f"%{current}%",),
                )
            else:
                # Ohne Eingabe: Zuletzt aktive Spieler anzeigen
                cursor = await db.execute(
                    "SELECT DISTINCT player_name FROM player_sessions "
                    "ORDER BY join_time DESC LIMIT 25",
                )
            rows = await cursor.fetchall()
            return [
                app_commands.Choice(name=row[0], value=row[0])
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Autocomplete-Fehler: {e}")
            return []

    # ==================================================================
    # Datenbank-Abfragen
    # ==================================================================

    async def _get_total_playtime(self, player_name: str) -> int:
        """Gesamtspielzeit eines Spielers in Minuten."""
        db = await get_db()
        cursor = await db.execute(
            "SELECT SUM(duration_minutes) FROM player_sessions "
            "WHERE player_name = ?",
            (player_name,),
        )
        row = await cursor.fetchone()
        return row[0] or 0 if row else 0

    async def _get_session_count(self, player_name: str, days: int) -> int:
        """Anzahl der Sessions in den letzten N Tagen."""
        db = await get_db()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = await db.execute(
            "SELECT COUNT(*) FROM player_sessions "
            "WHERE player_name = ? AND join_time >= ?",
            (player_name, since),
        )
        row = await cursor.fetchone()
        return row[0] or 0 if row else 0

    async def _get_favorite_server(self, player_name: str) -> Optional[str]:
        """Server mit der meisten Spielzeit fuer einen Spieler."""
        db = await get_db()
        cursor = await db.execute(
            "SELECT server_id, SUM(duration_minutes) as total "
            "FROM player_sessions WHERE player_name = ? "
            "GROUP BY server_id ORDER BY total DESC LIMIT 1",
            (player_name,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def _get_first_last_login(
        self, player_name: str
    ) -> tuple[Optional[str], Optional[str]]:
        """Erster und letzter Login eines Spielers."""
        db = await get_db()
        cursor = await db.execute(
            "SELECT MIN(join_time), MAX(join_time) FROM player_sessions "
            "WHERE player_name = ?",
            (player_name,),
        )
        row = await cursor.fetchone()
        if row:
            return row[0], row[1]
        return None, None

    async def _get_current_session(
        self, player_name: str
    ) -> Optional[tuple[str, str]]:
        """Aktuelle Session (falls online). Gibt (server_id, join_time) zurueck."""
        db = await get_db()
        cursor = await db.execute(
            "SELECT server_id, join_time FROM player_sessions "
            "WHERE player_name = ? AND leave_time IS NULL LIMIT 1",
            (player_name,),
        )
        row = await cursor.fetchone()
        if row:
            return row[0], row[1]
        return None

    async def _get_rank(self, player_name: str) -> int:
        """Rang des Spielers im Gesamtspielzeit-Leaderboard (1-basiert)."""
        total = await self._get_total_playtime(player_name)
        db = await get_db()
        cursor = await db.execute(
            "SELECT COUNT(*) + 1 FROM "
            "(SELECT player_name, SUM(duration_minutes) as total "
            "FROM player_sessions GROUP BY player_name) WHERE total > ?",
            (total,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 1

    async def _get_leaderboard(
        self, days: Optional[int] = None, limit: int = 10
    ) -> list[tuple[str, int]]:
        """Top-Spieler nach Spielzeit.

        Args:
            days: Zeitraum in Tagen (None = gesamt).
            limit: Maximale Anzahl der Eintraege.

        Returns:
            Liste von (player_name, total_minutes) Tupeln.
        """
        db = await get_db()
        if days is not None:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            cursor = await db.execute(
                "SELECT player_name, SUM(duration_minutes) as total "
                "FROM player_sessions WHERE join_time >= ? "
                "GROUP BY player_name ORDER BY total DESC LIMIT ?",
                (since, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT player_name, SUM(duration_minutes) as total "
                "FROM player_sessions "
                "GROUP BY player_name ORDER BY total DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [(row[0], row[1] or 0) for row in rows]

    async def _player_exists(self, player_name: str) -> bool:
        """Prueft ob ein Spieler in der Datenbank existiert."""
        db = await get_db()
        cursor = await db.execute(
            "SELECT 1 FROM player_sessions WHERE player_name = ? LIMIT 1",
            (player_name,),
        )
        return await cursor.fetchone() is not None

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  /profil [spieler] — Detailliertes Spieler-Profil            ║
    # ╚════════════════════════════════════════════════════════════════╝

    @app_commands.command(
        name="profil",
        description="Zeigt das Profil eines Spielers mit Statistiken an",
    )
    @app_commands.describe(
        spieler="Name des Spielers (Autocomplete verfuegbar)",
    )
    @app_commands.autocomplete(spieler=_player_name_autocomplete)
    async def profil_command(
        self,
        interaction: discord.Interaction,
        spieler: Optional[str] = None,
    ) -> None:
        """Detailliertes Spieler-Profil mit Spielzeit, Sessions und Rang."""
        await interaction.response.defer()

        # Ohne Spielername: Hinweis anzeigen
        if not spieler:
            await interaction.followup.send(
                "Bitte gib einen Spielernamen an: `/profil <spieler>`",
                ephemeral=True,
            )
            return

        # Pruefen ob Spieler existiert
        if not await self._player_exists(spieler):
            await interaction.followup.send(
                f"Spieler **{spieler}** wurde nicht gefunden.",
                ephemeral=True,
            )
            return

        try:
            # Alle Daten parallel abfragen
            total_minutes = await self._get_total_playtime(spieler)
            sessions_7d = await self._get_session_count(spieler, 7)
            sessions_30d = await self._get_session_count(spieler, 30)
            favorite_server = await self._get_favorite_server(spieler)
            first_login, last_login = await self._get_first_last_login(spieler)
            current_session = await self._get_current_session(spieler)
            rank = await self._get_rank(spieler)

            # Embed erstellen
            embed = discord.Embed(
                title=f"Profil — {spieler}",
                color=0x2ecc71 if current_session else 0x5865F2,
            )

            # Online-Status
            if current_session:
                server_id, join_time = current_session
                embed.description = f"Aktuell online auf **{server_id}**"
                embed.add_field(
                    name="Aktuelle Session",
                    value=f"Seit {_format_datetime(join_time)}",
                    inline=False,
                )

            # Spielzeit und Rang
            embed.add_field(
                name="Gesamtspielzeit",
                value=_format_playtime(total_minutes),
                inline=True,
            )
            embed.add_field(
                name="Rang",
                value=f"#{rank}",
                inline=True,
            )
            embed.add_field(
                name="Lieblingsserver",
                value=str(favorite_server) if favorite_server else "Keiner",
                inline=True,
            )

            # Sessions
            embed.add_field(
                name="Sessions (7 Tage)",
                value=str(sessions_7d),
                inline=True,
            )
            embed.add_field(
                name="Sessions (30 Tage)",
                value=str(sessions_30d),
                inline=True,
            )
            # Leerfeld fuer gleichmaessiges Layout
            embed.add_field(name="\u200b", value="\u200b", inline=True)

            # Erster und letzter Login
            embed.add_field(
                name="Erster Login",
                value=_format_datetime(first_login),
                inline=True,
            )
            embed.add_field(
                name="Letzter Login",
                value=_format_datetime(last_login),
                inline=True,
            )

            embed.set_footer(text=f"Spieler-Statistiken | Rang #{rank}")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Fehler beim Laden des Profils fuer '{spieler}': {e}", exc_info=True)
            await interaction.followup.send(
                "Fehler beim Laden der Spieler-Statistiken.",
                ephemeral=True,
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  /spieler_leaderboard [zeitraum] — Top 10 nach Spielzeit     ║
    # ╚════════════════════════════════════════════════════════════════╝

    @app_commands.command(
        name="spieler_leaderboard",
        description="Zeigt die Top 10 Spieler nach Spielzeit an",
    )
    @app_commands.describe(
        zeitraum="Zeitraum: woche (7 Tage), monat (30 Tage), gesamt (Standard)",
    )
    @app_commands.choices(
        zeitraum=[
            app_commands.Choice(name="Woche (7 Tage)", value="woche"),
            app_commands.Choice(name="Monat (30 Tage)", value="monat"),
            app_commands.Choice(name="Gesamt", value="gesamt"),
        ]
    )
    async def leaderboard_command(
        self,
        interaction: discord.Interaction,
        zeitraum: Optional[str] = "gesamt",
    ) -> None:
        """Top 10 Spieler nach Spielzeit als Embed anzeigen."""
        await interaction.response.defer()

        # Zeitraum in Tage umwandeln
        if zeitraum == "woche":
            days = 7
            zeitraum_text = "Letzte 7 Tage"
        elif zeitraum == "monat":
            days = 30
            zeitraum_text = "Letzte 30 Tage"
        else:
            days = None
            zeitraum_text = "Gesamt"

        try:
            top_players = await self._get_leaderboard(days=days, limit=10)

            if not top_players:
                await interaction.followup.send(
                    "Keine Spieler-Daten fuer diesen Zeitraum vorhanden.",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title=f"Spieler-Leaderboard — {zeitraum_text}",
                color=0xf1c40f,
            )

            # Nummerierte Liste mit Medaillen fuer Top 3
            lines: list[str] = []
            for i, (name, total_minutes) in enumerate(top_players, start=1):
                playtime = _format_playtime(total_minutes)

                if i == 1:
                    prefix = "\U0001f947"
                elif i == 2:
                    prefix = "\U0001f948"
                elif i == 3:
                    prefix = "\U0001f949"
                else:
                    prefix = f"**{i}.**"

                lines.append(f"{prefix} **{name}** — {playtime}")

            embed.description = "\n".join(lines)
            embed.set_footer(text=f"Top 10 | {zeitraum_text}")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Fehler beim Laden des Leaderboards: {e}", exc_info=True)
            await interaction.followup.send(
                "Fehler beim Laden des Leaderboards.",
                ephemeral=True,
            )

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung fuer alle Commands in diesem Cog."""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung fuer diesen Befehl.", ephemeral=True
                )
            return

        cmd_name = interaction.command.name if interaction.command else "unknown"
        logger.error(f"Command-Fehler in {cmd_name}: {error}", exc_info=True)

        try:
            msg = f"Ein Fehler ist aufgetreten: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    """Registriert den Profile-Cog beim Bot."""
    await bot.add_cog(ProfileCog(bot))
