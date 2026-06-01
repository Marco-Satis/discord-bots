"""
F54: Geplanter Shutdown mit Countdown — Server herunterfahren mit Vorwarnung

Plant einen zeitgesteuerten Shutdown für einen Gameserver. Spieler werden
vorher per Discord und (bei Minecraft) per RCON In-Game gewarnt.

Warnungen bei: 30min, 15min, 5min, 1min vor dem Shutdown.
Bei 0 Minuten: systemctl stop {service}

Commands:
  /shutdown <server> <minuten> [grund]  — Shutdown in X Minuten planen
  /shutdown cancel                      — Geplanten Shutdown abbrechen
  /shutdown status                      — Geplante Shutdowns anzeigen
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.database.db_manager import get_db
from utils.logger import get_logger

logger = get_logger("cogs.shutdown")

# Server-ID auf systemd Service-Name
SERVER_SERVICES = {
    "satisfactory": "satisfactory.service",
    "mc_bmc": "minecraft-bmc.service",
    "mc_vanilla": "minecraft-vanilla.service",
}

# Lesbare Anzeigenamen
SERVER_DISPLAY_NAMES = {
    "satisfactory": "Satisfactory",
    "mc_bmc": "Minecraft BMC",
    "mc_vanilla": "Minecraft Vanilla",
}

# Warnungszeitpunkte in Minuten vor dem Shutdown
WARNING_THRESHOLDS = [30, 15, 5, 1]

# Choices für die Server-Auswahl im Slash Command
_SERVER_CHOICES = [
    app_commands.Choice(name="Satisfactory", value="satisfactory"),
    app_commands.Choice(name="Minecraft BMC", value="mc_bmc"),
    app_commands.Choice(name="Minecraft Vanilla", value="mc_vanilla"),
]


class ShutdownCog(commands.Cog):
    """Geplanter Server-Shutdown mit Countdown und Spielerwarnungen"""

    # ==================================================================
    # Command-Gruppe
    # ==================================================================

    shutdown_grp = app_commands.Group(
        name="shutdown",
        description="Server-Shutdown planen und verwalten",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Geplante Shutdowns: server_id -> {"shutdown_at": datetime, "reason": str, "task": Task}
        self._planned: dict[str, dict] = {}

        logger.info("ShutdownCog initialisiert")

    # ------------------------------------------------------------------
    # Cog Lifecycle
    # ------------------------------------------------------------------

    async def cog_load(self) -> None:
        """Countdown-Pruef-Task starten"""
        self._countdown_tick.start()
        logger.info("ShutdownCog geladen — Countdown-Task gestartet")

    async def cog_unload(self) -> None:
        """Alle laufenden Shutdowns abbrechen und Task stoppen"""
        self._countdown_tick.cancel()
        for server_id, info in list(self._planned.items()):
            task = info.get("task")
            if task and not task.done():
                task.cancel()
            logger.info(f"Geplanter Shutdown für {server_id} abgebrochen (Cog entladen)")
        self._planned.clear()
        logger.info("ShutdownCog entladen")

    # ------------------------------------------------------------------
    # Countdown-Task (prueft alle 30 Sekunden)
    # ------------------------------------------------------------------

    @tasks.loop(seconds=30)
    async def _countdown_tick(self) -> None:
        """
        Prueft alle 30 Sekunden ob Warnungen gesendet oder
        Shutdowns ausgefuehrt werden muessen.
        """
        now = datetime.now()

        for server_id in list(self._planned.keys()):
            info = self._planned.get(server_id)
            if info is None:
                continue

            shutdown_at: datetime = info["shutdown_at"]
            remaining = (shutdown_at - now).total_seconds()
            remaining_min = remaining / 60.0

            # Shutdown-Zeitpunkt erreicht oder überschritten
            if remaining <= 0:
                await self._execute_shutdown(server_id, info)
                continue

            # Warnungen prüfen
            sent: set = info.setdefault("warnings_sent", set())
            for threshold in WARNING_THRESHOLDS:
                if threshold in sent:
                    continue
                # Warnung senden wenn verbleibende Zeit unter dem Schwellwert liegt
                # (plus Puffer für den 30-Sekunden-Tick)
                if remaining_min <= threshold + 0.5:
                    await self._send_warning(server_id, info, threshold)
                    sent.add(threshold)

    @_countdown_tick.before_loop
    async def _before_countdown(self) -> None:
        """Wartet bis der Bot bereit ist"""
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Warnungen senden
    # ------------------------------------------------------------------

    async def _send_warning(
        self,
        server_id: str,
        info: dict,
        minutes_left: int,
    ) -> None:
        """Sendet eine Warnung per Discord und In-Game (Minecraft RCON)"""
        display = SERVER_DISPLAY_NAMES.get(server_id, server_id)
        reason = info.get("reason", "")
        reason_text = f" — Grund: {reason}" if reason else ""

        warning_msg = (
            f"**{display}** wird in **{minutes_left} Minute(n)** heruntergefahren"
            f"{reason_text}"
        )

        # Discord-Warnung im Channel senden
        channel_id = info.get("channel_id")
        if channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    embed = discord.Embed(
                        title=f"Shutdown-Warnung: {display}",
                        description=warning_msg,
                        color=0xFF6600 if minutes_left > 1 else 0xFF0000,
                        timestamp=datetime.now(),
                    )
                    embed.set_footer(text=f"Geplant von {info.get('planned_by', 'System')}")
                    await channel.send(embed=embed)
                except Exception as e:
                    logger.error(f"Discord-Warnung fehlgeschlagen: {e}")

        # In-Game Warnung via RCON (nur Minecraft)
        await self._send_ingame_warning(server_id, minutes_left, reason)

        logger.info(
            f"Shutdown-Warnung gesendet: {display} in {minutes_left}min"
        )

    async def _send_ingame_warning(
        self,
        server_id: str,
        minutes_left: int,
        reason: str = "",
    ) -> None:
        """Sendet In-Game Warnungen falls möglich"""
        reason_part = f" Grund: {reason}" if reason else ""

        # Minecraft: RCON say-Befehl
        if server_id.startswith("mc_"):
            mc_servers = getattr(self.bot, "mc_servers", {})
            # Server-ID-Mapping: mc_bmc -> BMC, mc_vanilla -> VANILLA
            mc_key = server_id.replace("mc_", "").upper()
            srv = mc_servers.get(mc_key) or mc_servers.get(server_id)
            if srv:
                try:
                    await srv.rcon_command(
                        f"say Server wird in {minutes_left} Minute(n) "
                        f"heruntergefahren!{reason_part}"
                    )
                except Exception as e:
                    logger.debug(
                        f"[{server_id}] RCON-Warnung fehlgeschlagen: {e}"
                    )

        # Satisfactory: Keine In-Game API verfügbar — nur loggen
        elif server_id == "satisfactory":
            logger.info(
                f"[satisfactory] Shutdown in {minutes_left}min "
                f"(keine In-Game-Warnung möglich)"
            )

    # ------------------------------------------------------------------
    # Shutdown ausführen
    # ------------------------------------------------------------------

    async def _execute_shutdown(self, server_id: str, info: dict) -> None:
        """Fuehrt den eigentlichen Shutdown per systemctl durch"""
        display = SERVER_DISPLAY_NAMES.get(server_id, server_id)
        service = SERVER_SERVICES.get(server_id)

        if not service:
            logger.error(f"Kein Service für Server '{server_id}' gefunden")
            self._planned.pop(server_id, None)
            return

        logger.info(f"Shutdown wird ausgefuehrt: {display} ({service})")

        # Health-Check unterdruecken (absichtlicher Stop → kein Auto-Restart)
        har = getattr(self.bot, "health_auto_restart", None)
        if har:
            # Server-Typ und ID für Health-Checker bestimmen
            if server_id == "satisfactory":
                har.suppress("sat", "main", duration_seconds=600)
            elif server_id.startswith("mc_"):
                mc_id = server_id.replace("mc_", "").upper()
                har.suppress("mc", mc_id, duration_seconds=600)

        # Letzte In-Game Warnung
        await self._send_ingame_warning(server_id, 0, info.get("reason", ""))

        # systemctl stop ausführen
        success = False
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "stop", service,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=60
            )

            if proc.returncode == 0:
                success = True
                logger.info(f"Server {display} erfolgreich gestoppt")
            else:
                error_msg = stderr.decode().strip() if stderr else "Unbekannter Fehler"
                logger.error(
                    f"Server {display} Stop fehlgeschlagen: {error_msg}"
                )
        except asyncio.TimeoutError:
            logger.error(f"Timeout beim Stoppen von {display}")
        except Exception as e:
            logger.error(f"Fehler beim Stoppen von {display}: {e}")

        # Discord-Bestaetigung senden
        channel_id = info.get("channel_id")
        if channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    if success:
                        embed = discord.Embed(
                            title=f"{display} heruntergefahren",
                            description="Server wurde erfolgreich gestoppt.",
                            color=0x2ECC71,
                            timestamp=datetime.now(),
                        )
                    else:
                        embed = discord.Embed(
                            title=f"{display} Shutdown fehlgeschlagen",
                            description="Der Server konnte nicht gestoppt werden. Siehe Logs.",
                            color=0xFF0000,
                            timestamp=datetime.now(),
                        )
                    embed.set_footer(
                        text=f"Geplant von {info.get('planned_by', 'System')}"
                    )
                    await channel.send(embed=embed)
                except Exception as e:
                    logger.error(f"Shutdown-Bestaetigung senden fehlgeschlagen: {e}")

        # Aus geplanten Shutdowns entfernen
        self._planned.pop(server_id, None)

    # ------------------------------------------------------------------
    # Slash Commands
    # ------------------------------------------------------------------

    @shutdown_grp.command(
        name="plan",
        description="Server-Shutdown in X Minuten planen",
    )
    @app_commands.describe(
        server="Server der heruntergefahren werden soll",
        minuten="Countdown in Minuten bis zum Shutdown",
        grund="Optionaler Grund für den Shutdown",
    )
    @app_commands.choices(server=_SERVER_CHOICES)
    async def shutdown_plan(
        self,
        interaction: discord.Interaction,
        server: str,
        minuten: int,
        grund: str = "",
    ) -> None:
        """Plant einen Server-Shutdown in X Minuten"""
        await interaction.response.defer(ephemeral=True)

        # Validierung
        if server not in SERVER_SERVICES:
            await interaction.followup.send(
                f"Unbekannter Server: `{server}`", ephemeral=True
            )
            return

        if minuten < 1 or minuten > 1440:
            await interaction.followup.send(
                "Minuten muessen zwischen 1 und 1440 (24h) liegen.",
                ephemeral=True,
            )
            return

        # Pruefen ob bereits ein Shutdown geplant ist
        if server in self._planned:
            existing = self._planned[server]
            remaining = (existing["shutdown_at"] - datetime.now()).total_seconds()
            remaining_min = max(0, int(remaining / 60))
            await interaction.followup.send(
                f"Fuer **{SERVER_DISPLAY_NAMES.get(server, server)}** ist "
                f"bereits ein Shutdown in {remaining_min} Minute(n) geplant.\n"
                f"Verwende `/shutdown cancel` zuerst.",
                ephemeral=True,
            )
            return

        # Shutdown planen
        shutdown_at = datetime.now() + timedelta(minutes=minuten)
        display = SERVER_DISPLAY_NAMES.get(server, server)

        self._planned[server] = {
            "shutdown_at": shutdown_at,
            "reason": grund,
            "planned_by": str(interaction.user),
            "channel_id": interaction.channel_id,
            "warnings_sent": set(),
        }

        # Bestaetigung
        shutdown_time_str = shutdown_at.strftime("%H:%M:%S")
        embed = discord.Embed(
            title=f"Shutdown geplant: {display}",
            color=0xFF9900,
            timestamp=datetime.now(),
        )
        embed.add_field(name="Countdown", value=f"{minuten} Minute(n)", inline=True)
        embed.add_field(name="Shutdown um", value=shutdown_time_str, inline=True)
        embed.add_field(
            name="Grund", value=grund or "Nicht angegeben", inline=False
        )
        embed.add_field(
            name="Warnungen bei",
            value=", ".join(
                f"{t}min" for t in WARNING_THRESHOLDS if t < minuten
            ) or "Keine (zu kurzer Countdown)",
            inline=False,
        )
        embed.set_footer(text=f"Geplant von {interaction.user.display_name}")

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Oeffentliche Ankuendigung im Channel
        reason_text = f"\n**Grund:** {grund}" if grund else ""
        public_embed = discord.Embed(
            title=f"Server-Shutdown geplant: {display}",
            description=(
                f"Der Server wird in **{minuten} Minute(n)** "
                f"heruntergefahren.{reason_text}"
            ),
            color=0xFF9900,
            timestamp=datetime.now(),
        )
        public_embed.set_footer(
            text=f"Geplant von {interaction.user.display_name}"
        )
        await interaction.channel.send(embed=public_embed)

        logger.info(
            f"Shutdown geplant: {display} in {minuten}min "
            f"von {interaction.user} — Grund: {grund or 'keiner'}"
        )

    @shutdown_grp.command(
        name="cancel",
        description="Geplanten Shutdown abbrechen",
    )
    @app_commands.describe(
        server="Server dessen Shutdown abgebrochen werden soll",
    )
    @app_commands.choices(server=_SERVER_CHOICES)
    async def shutdown_cancel(
        self,
        interaction: discord.Interaction,
        server: str,
    ) -> None:
        """Bricht einen geplanten Shutdown ab"""
        await interaction.response.defer(ephemeral=True)

        if server not in self._planned:
            await interaction.followup.send(
                f"Kein Shutdown für **{SERVER_DISPLAY_NAMES.get(server, server)}** geplant.",
                ephemeral=True,
            )
            return

        display = SERVER_DISPLAY_NAMES.get(server, server)
        info = self._planned.pop(server)

        # Task abbrechen falls vorhanden
        task = info.get("task")
        if task and not task.done():
            task.cancel()

        embed = discord.Embed(
            title=f"Shutdown abgebrochen: {display}",
            description="Der geplante Shutdown wurde abgebrochen.",
            color=0x2ECC71,
            timestamp=datetime.now(),
        )
        embed.set_footer(
            text=f"Abgebrochen von {interaction.user.display_name}"
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Oeffentliche Nachricht
        channel_id = info.get("channel_id")
        if channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    public_embed = discord.Embed(
                        title=f"Shutdown abgebrochen: {display}",
                        description="Der geplante Server-Shutdown wurde abgebrochen.",
                        color=0x2ECC71,
                        timestamp=datetime.now(),
                    )
                    public_embed.set_footer(
                        text=f"Abgebrochen von {interaction.user.display_name}"
                    )
                    await channel.send(embed=public_embed)
                except Exception as e:
                    logger.error(f"Cancel-Nachricht senden fehlgeschlagen: {e}")

        # In-Game Entwarnung (Minecraft)
        if server.startswith("mc_"):
            mc_servers = getattr(self.bot, "mc_servers", {})
            mc_key = server.replace("mc_", "").upper()
            srv = mc_servers.get(mc_key) or mc_servers.get(server)
            if srv:
                try:
                    await srv.rcon_command(
                        "say Geplanter Shutdown wurde ABGEBROCHEN. Server bleibt online."
                    )
                except Exception as e:
                    logger.debug(f"RCON-Entwarnung fehlgeschlagen: {e}")

        logger.info(
            f"Shutdown abgebrochen: {display} von {interaction.user}"
        )

    @shutdown_grp.command(
        name="status",
        description="Geplante Shutdowns anzeigen",
    )
    async def shutdown_status(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Zeigt alle geplanten Shutdowns an"""
        await interaction.response.defer(ephemeral=True)

        if not self._planned:
            await interaction.followup.send(
                "Keine Shutdowns geplant.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Geplante Shutdowns",
            color=0xFF9900,
            timestamp=datetime.now(),
        )

        for server_id, info in self._planned.items():
            display = SERVER_DISPLAY_NAMES.get(server_id, server_id)
            shutdown_at: datetime = info["shutdown_at"]
            remaining = (shutdown_at - datetime.now()).total_seconds()
            remaining_min = max(0, int(remaining / 60))
            reason = info.get("reason", "") or "Kein Grund"
            planned_by = info.get("planned_by", "Unbekannt")

            embed.add_field(
                name=display,
                value=(
                    f"Shutdown um: {shutdown_at.strftime('%H:%M:%S')}\n"
                    f"Verbleibend: {remaining_min} Minute(n)\n"
                    f"Grund: {reason}\n"
                    f"Geplant von: {planned_by}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

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
            logger.error(f"Shutdown Command-Fehler: {error}", exc_info=True)
            msg = f"Fehler: {str(error)[:200]}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Cog laden"""
    await bot.add_cog(ShutdownCog(bot))
