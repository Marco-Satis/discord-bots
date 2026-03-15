"""
Notify Cog — F40: Spieler-Benachrichtigungssystem (Opt-in)

Spieler koennen sich fuer Server-Events anmelden und erhalten
DMs bei Statusaenderungen ihrer abonnierten Server.

Command-Struktur (app_commands.Group):
  /notify subscribe <server>   — Benachrichtigungen fuer einen Server abonnieren
  /notify unsubscribe <server> — Abonnement aufheben
  /notify list                 — Eigene Abonnements anzeigen

Unterstuetzte Events:
  server_online, server_offline, update_available, planned_restart

Oeffentliche Methode:
  notify_subscribers(server_id, event_type, message)
    — Sendet DMs an alle Abonnenten eines Servers.
      Cooldown: Gleicher event_type+server_id wird max. alle 5 Minuten gesendet.

Persistenz: SQLite-Tabelle 'notify_subscriptions'
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from modules.database.db_manager import get_db
from utils.logger import get_logger

logger = get_logger("cogs.notify")

# SQL fuer Tabellenerstellung
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS notify_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    server_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, server_id)
)
"""

# Verfuegbare Server als Choices
_SERVER_CHOICES = [
    app_commands.Choice(name="Satisfactory", value="satisfactory"),
    app_commands.Choice(name="Minecraft BMC", value="mc_bmc"),
    app_commands.Choice(name="Minecraft Vanilla", value="mc_vanilla"),
]

# Lesbare Server-Namen fuer Anzeige
_SERVER_LABELS: dict[str, str] = {
    "satisfactory": "Satisfactory",
    "mc_bmc": "Minecraft BMC",
    "mc_vanilla": "Minecraft Vanilla",
}

# Lesbare Event-Typen fuer Anzeige
_EVENT_LABELS: dict[str, str] = {
    "server_online": "Server Online",
    "server_offline": "Server Offline",
    "update_available": "Update verfuegbar",
    "planned_restart": "Geplanter Neustart",
}

# Cooldown in Sekunden (5 Minuten)
_COOLDOWN_SECONDS = 300


class NotifyCog(commands.Cog):
    """Spieler-Benachrichtigungssystem: Opt-in fuer Server-Events per DM"""

    notify_grp = app_commands.Group(
        name="notify",
        description="Benachrichtigungen — Server-Events per DM abonnieren",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Cooldown-Tracker: Key = "event_type:server_id", Value = Timestamp
        self._cooldowns: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Cog Lifecycle
    # ------------------------------------------------------------------

    async def cog_load(self) -> None:
        """Tabelle erstellen falls nicht vorhanden und Cog initialisieren"""
        await self._ensure_table()
        logger.info("Notify-Cog geladen")

    async def cog_unload(self) -> None:
        logger.info("Notify-Cog entladen")

    # ------------------------------------------------------------------
    # Datenbank-Hilfsmethoden
    # ------------------------------------------------------------------

    async def _ensure_table(self) -> None:
        """Stellt sicher, dass die Tabelle 'notify_subscriptions' existiert"""
        try:
            db = await get_db()
            await db.execute(CREATE_TABLE_SQL)
            await db.commit()
            logger.info("Tabelle 'notify_subscriptions' sichergestellt")
        except Exception as e:
            logger.error(f"Fehler beim Erstellen der Tabelle: {e}", exc_info=True)

    async def _get_subscriptions(self, user_id: str) -> list[dict]:
        """
        Alle Abonnements eines Users aus der Datenbank holen.

        Args:
            user_id: Discord User ID als String

        Returns:
            Liste von Dicts mit server_id und created_at
        """
        db = await get_db()
        cursor = await db.execute(
            "SELECT server_id, created_at FROM notify_subscriptions "
            "WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            {"server_id": row[0], "created_at": row[1]}
            for row in rows
        ]

    async def _is_subscribed(self, user_id: str, server_id: str) -> bool:
        """Prueft ob ein User bereits fuer einen Server abonniert ist"""
        db = await get_db()
        cursor = await db.execute(
            "SELECT 1 FROM notify_subscriptions "
            "WHERE user_id = ? AND server_id = ? LIMIT 1",
            (user_id, server_id),
        )
        return await cursor.fetchone() is not None

    async def _get_subscribers(self, server_id: str) -> list[str]:
        """
        Alle Abonnenten eines Servers aus der Datenbank holen.

        Args:
            server_id: Server-Kennung (z.B. 'satisfactory')

        Returns:
            Liste von User-IDs als Strings
        """
        db = await get_db()
        cursor = await db.execute(
            "SELECT user_id FROM notify_subscriptions WHERE server_id = ?",
            (server_id,),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    # ==================================================================
    # /notify subscribe <server>
    # ==================================================================

    @notify_grp.command(
        name="subscribe",
        description="Benachrichtigungen fuer einen Server abonnieren (DM)",
    )
    @app_commands.describe(
        server="Server fuer den du Benachrichtigungen erhalten moechtest",
    )
    @app_commands.choices(server=_SERVER_CHOICES)
    async def notify_subscribe(
        self,
        interaction: discord.Interaction,
        server: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        server_id = server.value

        # Pruefen ob bereits abonniert
        if await self._is_subscribed(user_id, server_id):
            await interaction.followup.send(
                f"Du bist bereits fuer **{server.name}** angemeldet.",
                ephemeral=True,
            )
            return

        # Abonnement in Datenbank speichern
        try:
            db = await get_db()
            await db.execute(
                "INSERT INTO notify_subscriptions (user_id, server_id, created_at) "
                "VALUES (?, ?, ?)",
                (user_id, server_id, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        except Exception as e:
            logger.error(
                f"Fehler beim Erstellen des Abonnements fuer {interaction.user}: {e}",
                exc_info=True,
            )
            await interaction.followup.send(
                f"Fehler beim Speichern: {e}", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Benachrichtigung abonniert",
            description=(
                f"Du erhaeltst jetzt DMs bei Events fuer **{server.name}**.\n\n"
                f"Events: Server Online/Offline, Updates, geplante Neustarts\n\n"
                f"Abmelden mit: `/notify unsubscribe {server_id}`"
            ),
            color=0x2ECC71,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"Notify-Abo erstellt: {interaction.user} -> {server_id}"
        )

    # ==================================================================
    # /notify unsubscribe <server>
    # ==================================================================

    @notify_grp.command(
        name="unsubscribe",
        description="Benachrichtigungen fuer einen Server abbestellen",
    )
    @app_commands.describe(
        server="Server fuer den du keine Benachrichtigungen mehr erhalten moechtest",
    )
    @app_commands.choices(server=_SERVER_CHOICES)
    async def notify_unsubscribe(
        self,
        interaction: discord.Interaction,
        server: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        server_id = server.value

        # Pruefen ob Abonnement existiert
        if not await self._is_subscribed(user_id, server_id):
            await interaction.followup.send(
                f"Du bist nicht fuer **{server.name}** angemeldet.",
                ephemeral=True,
            )
            return

        # Abonnement aus Datenbank loeschen
        try:
            db = await get_db()
            await db.execute(
                "DELETE FROM notify_subscriptions "
                "WHERE user_id = ? AND server_id = ?",
                (user_id, server_id),
            )
            await db.commit()
        except Exception as e:
            logger.error(
                f"Fehler beim Loeschen des Abonnements fuer {interaction.user}: {e}",
                exc_info=True,
            )
            await interaction.followup.send(
                f"Fehler beim Loeschen: {e}", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Benachrichtigung abbestellt",
            description=f"Du erhaeltst keine DMs mehr fuer **{server.name}**.",
            color=0xE74C3C,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"Notify-Abo geloescht: {interaction.user} -> {server_id}"
        )

    # ==================================================================
    # /notify list
    # ==================================================================

    @notify_grp.command(
        name="list",
        description="Eigene Benachrichtigungs-Abonnements anzeigen",
    )
    async def notify_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        subscriptions = await self._get_subscriptions(user_id)

        if not subscriptions:
            await interaction.followup.send(
                "Du hast keine aktiven Benachrichtigungen.\n"
                "Abonniere mit `/notify subscribe <server>`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Deine Benachrichtigungen ({len(subscriptions)})",
            color=0x5865F2,
        )

        lines: list[str] = []
        for sub in subscriptions:
            server_label = _SERVER_LABELS.get(sub["server_id"], sub["server_id"])
            # Erstellungsdatum formatieren
            try:
                dt = datetime.fromisoformat(sub["created_at"])
                created_str = dt.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                created_str = sub["created_at"]

            lines.append(f"**{server_label}** — seit {created_str}")

        embed.description = "\n".join(lines)
        embed.set_footer(
            text="Abmelden mit /notify unsubscribe <server>"
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # Oeffentliche Methode: Abonnenten benachrichtigen
    # ==================================================================

    async def notify_subscribers(
        self,
        server_id: str,
        event_type: str,
        message: str,
    ) -> None:
        """
        Sendet DMs an alle Abonnenten eines Servers.

        Cooldown: Gleicher event_type+server_id wird max. alle 5 Minuten gesendet.
        DM-Fehler werden graceful behandelt (User hat DMs deaktiviert o.ae.).

        Args:
            server_id: Server-Kennung (z.B. 'satisfactory', 'mc_bmc')
            event_type: Event-Typ (z.B. 'server_online', 'server_offline')
            message: Nachrichtentext fuer die DM
        """
        # Cooldown pruefen
        cooldown_key = f"{event_type}:{server_id}"
        now = time.monotonic()
        last_sent = self._cooldowns.get(cooldown_key, 0.0)

        if now - last_sent < _COOLDOWN_SECONDS:
            remaining = int(_COOLDOWN_SECONDS - (now - last_sent))
            logger.debug(
                f"Notify-Cooldown aktiv fuer {cooldown_key}: "
                f"noch {remaining}s verbleibend, ueberspringe"
            )
            return

        # Cooldown aktualisieren (vor dem Senden, um Race Conditions zu vermeiden)
        self._cooldowns[cooldown_key] = now

        # Abonnenten aus der Datenbank holen
        try:
            subscriber_ids = await self._get_subscribers(server_id)
        except Exception as e:
            logger.error(
                f"Fehler beim Laden der Abonnenten fuer {server_id}: {e}",
                exc_info=True,
            )
            return

        if not subscriber_ids:
            logger.debug(f"Keine Abonnenten fuer {server_id}, ueberspringe")
            return

        # Event-Label fuer Embed
        server_label = _SERVER_LABELS.get(server_id, server_id)
        event_label = _EVENT_LABELS.get(event_type, event_type)

        # Embed fuer die DM erstellen
        embed = discord.Embed(
            title=f"{server_label} — {event_label}",
            description=message,
            color=self._event_color(event_type),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="Abmelden mit /notify unsubscribe")

        # DMs an alle Abonnenten senden
        sent_count = 0
        failed_count = 0

        for user_id_str in subscriber_ids:
            try:
                user_id = int(user_id_str)
                user = self.bot.get_user(user_id)
                if user is None:
                    try:
                        user = await self.bot.fetch_user(user_id)
                    except (discord.NotFound, discord.HTTPException):
                        logger.warning(
                            f"Notify: User {user_id_str} nicht gefunden, ueberspringe"
                        )
                        failed_count += 1
                        continue

                await user.send(embed=embed)
                sent_count += 1

            except discord.Forbidden:
                # User hat DMs deaktiviert — graceful behandeln
                logger.debug(
                    f"Notify: DMs deaktiviert fuer User {user_id_str}, ueberspringe"
                )
                failed_count += 1
            except discord.HTTPException as e:
                logger.warning(
                    f"Notify: DM an User {user_id_str} fehlgeschlagen: {e}"
                )
                failed_count += 1
            except Exception as e:
                logger.error(
                    f"Notify: Unerwarteter Fehler beim Senden an {user_id_str}: {e}",
                    exc_info=True,
                )
                failed_count += 1

        logger.info(
            f"Notify: {event_type} fuer {server_id} — "
            f"{sent_count} gesendet, {failed_count} fehlgeschlagen "
            f"(von {len(subscriber_ids)} Abonnenten)"
        )

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    @staticmethod
    def _event_color(event_type: str) -> int:
        """Farbe fuer das Embed je nach Event-Typ"""
        colors = {
            "server_online": 0x2ECC71,    # Gruen
            "server_offline": 0xE74C3C,   # Rot
            "update_available": 0x3498DB,  # Blau
            "planned_restart": 0xF39C12,   # Orange
        }
        return colors.get(event_type, 0x5865F2)

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung fuer alle Commands in dieser Cog"""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung fuer diesen Befehl.", ephemeral=True
                )
            return

        cmd_name = interaction.command.name if interaction.command else "unknown"
        logger.error(f"Command error in {cmd_name}: {error}", exc_info=True)
        try:
            msg = f"Ein Fehler ist aufgetreten: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    """Cog laden"""
    await bot.add_cog(NotifyCog(bot))
