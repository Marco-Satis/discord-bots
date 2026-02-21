"""
Giveaway Cog — Phase 11h
Cog fuer den Admin Bot: Verlosungen erstellen, verwalten und automatisch beenden.

Commands:
  /giveaway create <preis> <dauer_minuten> [gewinner] [channel]  — Neues Giveaway
  /giveaway end <message_id>                                     — Vorzeitig beenden
  /giveaway reroll <message_id>                                  — Neue Gewinner ziehen
  /giveaway cancel <message_id>                                  — Ohne Gewinner abbrechen
  /giveaway list                                                 — Aktive Giveaways anzeigen

Features:
  - Persistent View mit "Teilnehmen"-Button (ueberlebt Bot-Neustarts)
  - Anforderungen: Mindest-Level, Rollen-Pflicht, Mindest-Mitgliedschaft
  - Automatisches Beenden durch Background-Task (alle 30s)
  - Gewinner-Benachrichtigung: Tag im Kanal + DM
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.giveaways import GiveawayManager
from utils.logger import get_logger
from utils.permissions import admin_only

logger = get_logger("cogs.giveaway")

# Custom-ID-Prefix fuer den Teilnehmen-Button (persistent)
BUTTON_CUSTOM_ID_PREFIX = "giveaway_join:"

# Farben
COLOR_ACTIVE = 0xf1c40f    # Gelb — aktives Giveaway
COLOR_ENDED = 0x2ecc71     # Gruen — Giveaway beendet
COLOR_CANCELLED = 0x95a5a6  # Grau — Giveaway abgebrochen
COLOR_NO_WINNER = 0xe74c3c  # Rot — Keine Gewinner (zu wenige Teilnehmer)


# ==================================================================
# Persistent View — ueberlebt Bot-Neustarts
# ==================================================================

class GiveawayView(discord.ui.View):
    """
    Persistent View fuer den Giveaway-Teilnehmen-Button.

    Verwendet eine feste custom_id mit Prefix, damit Discord die
    Interaktion auch nach einem Bot-Neustart zuordnen kann.
    """

    def __init__(self, manager: GiveawayManager) -> None:
        super().__init__(timeout=None)  # Kein Timeout — persistent
        self.manager = manager

    @discord.ui.button(
        label="Teilnehmen",
        style=discord.ButtonStyle.green,
        emoji="\U0001f389",
        custom_id="giveaway_join:persistent",
    )
    async def join_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """
        Teilnehmen/Austragen-Toggle fuer Giveaways.

        Prueft Anforderungen (Rolle, Mitgliedschaftsdauer) und
        fuegt den User hinzu oder entfernt ihn.
        """
        message_id = interaction.message.id
        user_id = interaction.user.id

        # Pruefen ob Giveaway existiert und aktiv ist
        giveaway = self.manager.get_giveaway(message_id)
        if not giveaway:
            await interaction.response.send_message(
                "Dieses Giveaway existiert nicht mehr.", ephemeral=True
            )
            return

        if giveaway.get("ended"):
            await interaction.response.send_message(
                "Dieses Giveaway ist bereits beendet.", ephemeral=True
            )
            return

        # Host darf nicht teilnehmen
        if user_id == giveaway.get("host_id"):
            await interaction.response.send_message(
                "Du kannst an deinem eigenen Giveaway nicht teilnehmen.",
                ephemeral=True,
            )
            return

        # Anforderungen pruefen
        requirements = giveaway.get("requirements", {})
        member = interaction.guild.get_member(user_id) if interaction.guild else None

        if member and not self.manager.is_participant(message_id, user_id):
            # Nur beim Hinzufuegen pruefen, nicht beim Entfernen

            # Rollen-Anforderung
            required_role_id = requirements.get("role_id")
            if required_role_id:
                has_role = any(r.id == required_role_id for r in member.roles)
                if not has_role:
                    role = interaction.guild.get_role(required_role_id)
                    role_name = role.name if role else f"ID: {required_role_id}"
                    await interaction.response.send_message(
                        f"Du benoetigst die Rolle **{role_name}** um teilzunehmen.",
                        ephemeral=True,
                    )
                    return

            # Mindest-Mitgliedschaftsdauer
            min_days = requirements.get("min_days", 0)
            if min_days > 0 and member.joined_at:
                membership_days = (
                    datetime.now(timezone.utc) - member.joined_at
                ).days
                if membership_days < min_days:
                    await interaction.response.send_message(
                        f"Du musst mindestens {min_days} Tage Mitglied sein "
                        f"um teilzunehmen. (Aktuell: {membership_days} Tage)",
                        ephemeral=True,
                    )
                    return

            # Mindest-Level (wird nur geprueft wenn > 0)
            # Level-System ist extern — wir pruefen via bot.level_manager
            min_level = requirements.get("min_level", 0)
            if min_level > 0:
                level_mgr = getattr(interaction.client, "level_manager", None)
                if level_mgr:
                    user_level = level_mgr.get_level(user_id)
                    if user_level < min_level:
                        await interaction.response.send_message(
                            f"Du benoetigst mindestens Level {min_level} "
                            f"um teilzunehmen. (Aktuell: Level {user_level})",
                            ephemeral=True,
                        )
                        return

        # Toggle: Teilnehmen / Austragen
        if self.manager.is_participant(message_id, user_id):
            # Austragen
            self.manager.remove_participant(message_id, user_id)
            await interaction.response.send_message(
                "Du nimmst nicht mehr am Giveaway teil.", ephemeral=True
            )
        else:
            # Teilnehmen
            success = self.manager.add_participant(message_id, user_id)
            if success:
                await interaction.response.send_message(
                    "Du nimmst jetzt am Giveaway teil! Viel Glueck!",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Teilnahme fehlgeschlagen. Bitte versuche es erneut.",
                    ephemeral=True,
                )
                return

        # Embed mit aktueller Teilnehmer-Anzahl aktualisieren
        try:
            count = self.manager.get_participant_count(message_id)
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            if embed:
                # Teilnehmer-Feld aktualisieren
                updated_embed = self._update_participant_count(embed, count)
                await interaction.message.edit(embed=updated_embed)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Giveaway-Embed aktualisieren fehlgeschlagen: {e}")

    @staticmethod
    def _update_participant_count(
        embed: discord.Embed, count: int
    ) -> discord.Embed:
        """
        Teilnehmer-Feld im Embed aktualisieren.

        Sucht das Feld "Teilnehmer" und aktualisiert den Wert.
        Falls nicht gefunden, wird es angehaengt.

        Args:
            embed: Das aktuelle Embed
            count: Neue Teilnehmer-Anzahl

        Returns:
            Das aktualisierte Embed
        """
        # Vorhandenes Feld suchen und aktualisieren
        for i, field in enumerate(embed.fields):
            if field.name == "Teilnehmer":
                embed.set_field_at(
                    i,
                    name="Teilnehmer",
                    value=str(count),
                    inline=True,
                )
                return embed

        # Feld existiert noch nicht — anhaengen
        embed.add_field(name="Teilnehmer", value=str(count), inline=True)
        return embed


# ==================================================================
# Giveaway Cog
# ==================================================================

class GiveawayCog(commands.Cog):
    """
    Giveaway-System fuer den Admin Bot.

    Verwaltet Verlosungen mit persistenten Buttons, automatischem
    Beenden, Gewinner-Benachrichtigung und Anforderungs-Pruefung.
    """

    giveaway_grp = app_commands.Group(
        name="giveaway",
        description="Giveaway-Verwaltung (Verlosungen)",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.manager = GiveawayManager()
        # Manager auf dem Bot verfuegbar machen
        bot.giveaway_manager = self.manager

        # Persistent View registrieren
        self._view = GiveawayView(self.manager)
        bot.add_view(self._view)

        logger.info("GiveawayCog initialisiert")

    async def cog_load(self) -> None:
        """DB-Daten laden und Background-Task starten."""
        # F28: Giveaway-Daten aus SQLite laden (ueberschreibt JSON-Fallback)
        try:
            await self.manager.load_from_db()
            logger.info("Giveaway-Daten aus SQLite geladen")
        except Exception as e:
            logger.warning(f"SQLite-Load fehlgeschlagen, nutze JSON-Fallback: {e}")
        self.check_giveaways.start()
        logger.info("Giveaway-Background-Task gestartet")

    async def cog_unload(self) -> None:
        """Background-Task stoppen wenn Cog entladen wird"""
        self.check_giveaways.cancel()
        logger.info("Giveaway-Background-Task gestoppt")

    # ==================================================================
    # Background-Task: Abgelaufene Giveaways automatisch beenden
    # ==================================================================

    @tasks.loop(seconds=30)
    async def check_giveaways(self) -> None:
        """Prueft alle 30 Sekunden ob Giveaways abgelaufen sind"""
        expired = self.manager.get_expired()
        if not expired:
            return

        for giveaway in expired:
            message_id = giveaway.get("message_id")
            if not message_id:
                continue

            try:
                await self._end_and_announce(message_id)
            except Exception as e:
                logger.error(
                    f"Fehler beim automatischen Beenden von Giveaway "
                    f"{message_id}: {e}",
                    exc_info=True,
                )

    @check_giveaways.before_loop
    async def before_check_giveaways(self) -> None:
        """Warten bis Bot bereit ist"""
        await self.bot.wait_until_ready()

    # ==================================================================
    # Hilfsmethoden
    # ==================================================================

    def _build_giveaway_embed(
        self,
        prize: str,
        host: discord.Member | discord.User,
        duration_minutes: int,
        winners_count: int,
        participant_count: int = 0,
        requirements: Optional[dict] = None,
    ) -> discord.Embed:
        """
        Giveaway-Embed erstellen.

        Args:
            prize: Beschreibung des Preises
            host: Veranstalter
            duration_minutes: Laufzeit in Minuten
            winners_count: Anzahl der Gewinner
            participant_count: Aktuelle Teilnehmer-Anzahl
            requirements: Anforderungen (optional)

        Returns:
            Das erstellte Embed
        """
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        ends_at = now + timedelta(minutes=duration_minutes)

        embed = discord.Embed(
            title="\U0001f389 Giveaway \U0001f389",
            description=f"**{prize}**",
            color=COLOR_ACTIVE,
            timestamp=ends_at,
        )

        embed.add_field(name="Veranstalter", value=host.mention, inline=True)
        embed.add_field(name="Gewinner", value=str(winners_count), inline=True)
        embed.add_field(name="Teilnehmer", value=str(participant_count), inline=True)

        # Laufzeit anzeigen
        if duration_minutes >= 1440:
            days = duration_minutes // 1440
            hours = (duration_minutes % 1440) // 60
            duration_str = f"{days}d {hours}h" if hours else f"{days}d"
        elif duration_minutes >= 60:
            hours = duration_minutes // 60
            mins = duration_minutes % 60
            duration_str = f"{hours}h {mins}m" if mins else f"{hours}h"
        else:
            duration_str = f"{duration_minutes}m"

        embed.add_field(name="Dauer", value=duration_str, inline=True)

        # Anforderungen anzeigen
        if requirements:
            req_parts = []
            if requirements.get("min_level", 0) > 0:
                req_parts.append(f"Level {requirements['min_level']}+")
            if requirements.get("role_id"):
                req_parts.append(f"Rolle: <@&{requirements['role_id']}>")
            if requirements.get("min_days", 0) > 0:
                req_parts.append(f"Mitglied seit {requirements['min_days']}+ Tagen")

            if req_parts:
                embed.add_field(
                    name="Anforderungen",
                    value="\n".join(f"- {r}" for r in req_parts),
                    inline=False,
                )

        embed.set_footer(text="Endet")  # Discord zeigt Timestamp daneben an

        return embed

    def _build_ended_embed(
        self,
        prize: str,
        winner_ids: list[int],
        participant_count: int,
    ) -> discord.Embed:
        """
        Embed fuer beendetes Giveaway erstellen.

        Args:
            prize: Beschreibung des Preises
            winner_ids: Liste der Gewinner-IDs
            participant_count: Anzahl der Teilnehmer

        Returns:
            Das erstellte Embed
        """
        if winner_ids:
            winners_str = ", ".join(f"<@{uid}>" for uid in winner_ids)
            color = COLOR_ENDED
        else:
            winners_str = "Keine Gewinner (zu wenige Teilnehmer)"
            color = COLOR_NO_WINNER

        embed = discord.Embed(
            title="\U0001f389 Giveaway beendet \U0001f389",
            description=f"**{prize}**",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(name="Gewinner", value=winners_str, inline=False)
        embed.add_field(
            name="Teilnehmer gesamt",
            value=str(participant_count),
            inline=True,
        )

        return embed

    async def _end_and_announce(self, message_id: int) -> None:
        """
        Giveaway beenden, Embed aktualisieren und Gewinner benachrichtigen.

        Args:
            message_id: ID der Giveaway-Nachricht
        """
        # Giveaway-Daten holen bevor wir beenden
        giveaway = self.manager.get_giveaway(message_id)
        if not giveaway:
            return

        channel_id = giveaway.get("channel_id")
        prize = giveaway.get("prize", "Unbekannt")
        participant_count = len(giveaway.get("participants", []))

        # Giveaway beenden und Gewinner ziehen
        success, winner_ids = self.manager.end_giveaway(message_id)
        if not success:
            return

        # Kanal und Nachricht finden
        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning(
                    f"Giveaway-Kanal {channel_id} nicht erreichbar"
                )
                return

        # Original-Nachricht aktualisieren (Embed + Button deaktivieren)
        try:
            message = await channel.fetch_message(message_id)
            ended_embed = self._build_ended_embed(
                prize, winner_ids, participant_count
            )

            # View mit deaktiviertem Button
            disabled_view = discord.ui.View(timeout=None)
            disabled_button = discord.ui.Button(
                label="Beendet",
                style=discord.ButtonStyle.grey,
                emoji="\U0001f389",
                disabled=True,
                custom_id="giveaway_ended:disabled",
            )
            disabled_view.add_item(disabled_button)

            await message.edit(embed=ended_embed, view=disabled_view)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Giveaway-Nachricht aktualisieren fehlgeschlagen: {e}")

        # Gewinner im Kanal taggen
        if winner_ids:
            winners_mentions = ", ".join(f"<@{uid}>" for uid in winner_ids)
            try:
                await channel.send(
                    f"\U0001f389 Herzlichen Glueckwunsch {winners_mentions}! "
                    f"Ihr habt **{prize}** gewonnen!"
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Gewinner-Nachricht senden fehlgeschlagen: {e}")

            # Gewinner per DM benachrichtigen
            for uid in winner_ids:
                await self._notify_winner_dm(uid, prize, channel)

    async def _notify_winner_dm(
        self,
        user_id: int,
        prize: str,
        channel: discord.TextChannel,
    ) -> None:
        """
        Gewinner per DM ueber den Gewinn benachrichtigen.

        Args:
            user_id: Discord-ID des Gewinners
            prize: Beschreibung des Preises
            channel: Kanal in dem das Giveaway stattfand
        """
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            embed = discord.Embed(
                title="\U0001f389 Du hast gewonnen!",
                description=(
                    f"Herzlichen Glueckwunsch! Du hast **{prize}** "
                    f"im Giveaway in {channel.mention} gewonnen!\n\n"
                    f"Melde dich bei einem Admin um deinen Preis zu erhalten."
                ),
                color=COLOR_ENDED,
                timestamp=datetime.now(timezone.utc),
            )
            await user.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            logger.debug(f"Gewinner-DM an {user_id} konnte nicht gesendet werden")
        except Exception as e:
            logger.warning(f"Gewinner-DM fehlgeschlagen: {e}")

    # ==================================================================
    # Slash-Commands
    # ==================================================================

    @giveaway_grp.command(
        name="create",
        description="Neues Giveaway erstellen (Admin)",
    )
    @app_commands.describe(
        preis="Beschreibung des Preises",
        dauer_minuten="Laufzeit in Minuten",
        gewinner="Anzahl der Gewinner (Standard: 1)",
        channel="Kanal fuer das Giveaway (Standard: aktueller Kanal)",
        min_level="Mindest-Level zum Teilnehmen (0 = keins)",
        benoetigte_rolle="Rolle die zum Teilnehmen benoetigt wird",
        min_tage="Mindest-Mitgliedschaftsdauer in Tagen (0 = keine)",
    )
    @admin_only()
    async def giveaway_create(
        self,
        interaction: discord.Interaction,
        preis: str,
        dauer_minuten: int,
        gewinner: int = 1,
        channel: Optional[discord.TextChannel] = None,
        min_level: int = 0,
        benoetigte_rolle: Optional[discord.Role] = None,
        min_tage: int = 0,
    ) -> None:
        """Neues Giveaway erstellen und Embed mit Button senden"""
        await interaction.response.defer(ephemeral=True)

        # Validierung
        if dauer_minuten < 1:
            await interaction.followup.send(
                "Dauer muss mindestens 1 Minute sein.", ephemeral=True
            )
            return

        if dauer_minuten > 43200:  # Max 30 Tage
            await interaction.followup.send(
                "Maximale Dauer: 30 Tage (43200 Minuten).", ephemeral=True
            )
            return

        if gewinner < 1:
            await interaction.followup.send(
                "Es muss mindestens 1 Gewinner geben.", ephemeral=True
            )
            return

        if gewinner > 20:
            await interaction.followup.send(
                "Maximal 20 Gewinner pro Giveaway.", ephemeral=True
            )
            return

        if len(preis) > 256:
            await interaction.followup.send(
                "Preis-Beschreibung darf maximal 256 Zeichen lang sein.",
                ephemeral=True,
            )
            return

        # Ziel-Kanal bestimmen
        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.followup.send(
                "Giveaways koennen nur in Textkanaelen erstellt werden.",
                ephemeral=True,
            )
            return

        # Anforderungen zusammenstellen
        requirements = {
            "min_level": max(0, min_level),
            "role_id": benoetigte_rolle.id if benoetigte_rolle else None,
            "min_days": max(0, min_tage),
        }

        # Giveaway-Embed erstellen
        embed = self._build_giveaway_embed(
            prize=preis,
            host=interaction.user,
            duration_minutes=dauer_minuten,
            winners_count=gewinner,
            participant_count=0,
            requirements=requirements,
        )

        # View mit Teilnehmen-Button erstellen
        view = GiveawayView(self.manager)

        # Embed in den Ziel-Kanal senden
        try:
            giveaway_msg = await target_channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(
                f"Giveaway konnte nicht gesendet werden: {e}",
                ephemeral=True,
            )
            return

        # Giveaway im Manager speichern
        self.manager.create(
            channel_id=target_channel.id,
            guild_id=interaction.guild_id,
            message_id=giveaway_msg.id,
            prize=preis,
            duration_minutes=dauer_minuten,
            winners_count=gewinner,
            host_id=interaction.user.id,
            requirements=requirements,
        )

        # Bestaetigung an den Admin
        if dauer_minuten >= 60:
            hours = dauer_minuten // 60
            mins = dauer_minuten % 60
            duration_str = f"{hours}h {mins}m" if mins else f"{hours}h"
        else:
            duration_str = f"{dauer_minuten}m"

        await interaction.followup.send(
            f"Giveaway erstellt in {target_channel.mention}!\n"
            f"**Preis:** {preis}\n"
            f"**Dauer:** {duration_str}\n"
            f"**Gewinner:** {gewinner}",
            ephemeral=True,
        )

        logger.info(
            f"Giveaway erstellt: '{preis}' in #{target_channel.name} "
            f"(Dauer: {dauer_minuten}min, Gewinner: {gewinner}, "
            f"von {interaction.user})"
        )

    @giveaway_grp.command(
        name="end",
        description="Giveaway vorzeitig beenden und Gewinner ziehen (Admin)",
    )
    @app_commands.describe(
        message_id="Message-ID des Giveaway-Embeds",
    )
    @admin_only()
    async def giveaway_end(
        self,
        interaction: discord.Interaction,
        message_id: str,
    ) -> None:
        """Giveaway vorzeitig beenden"""
        await interaction.response.defer(ephemeral=True)

        try:
            msg_id = int(message_id)
        except ValueError:
            await interaction.followup.send(
                "Ungueltige Message-ID.", ephemeral=True
            )
            return

        giveaway = self.manager.get_giveaway(msg_id)
        if not giveaway:
            await interaction.followup.send(
                "Giveaway nicht gefunden.", ephemeral=True
            )
            return

        if giveaway.get("ended"):
            await interaction.followup.send(
                "Dieses Giveaway ist bereits beendet.", ephemeral=True
            )
            return

        # Giveaway beenden und Gewinner bekannt geben
        await self._end_and_announce(msg_id)

        # Bestaetigung
        giveaway_after = self.manager.get_giveaway(msg_id)
        winner_ids = giveaway_after.get("winner_ids", []) if giveaway_after else []

        if winner_ids:
            winners_str = ", ".join(f"<@{uid}>" for uid in winner_ids)
            await interaction.followup.send(
                f"Giveaway beendet! Gewinner: {winners_str}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "Giveaway beendet! Keine Gewinner (zu wenige Teilnehmer).",
                ephemeral=True,
            )

        logger.info(
            f"Giveaway vorzeitig beendet: {msg_id} (von {interaction.user})"
        )

    @giveaway_grp.command(
        name="reroll",
        description="Neue Gewinner fuer ein beendetes Giveaway ziehen (Admin)",
    )
    @app_commands.describe(
        message_id="Message-ID des Giveaway-Embeds",
    )
    @admin_only()
    async def giveaway_reroll(
        self,
        interaction: discord.Interaction,
        message_id: str,
    ) -> None:
        """Neue Gewinner ziehen"""
        await interaction.response.defer(ephemeral=True)

        try:
            msg_id = int(message_id)
        except ValueError:
            await interaction.followup.send(
                "Ungueltige Message-ID.", ephemeral=True
            )
            return

        giveaway = self.manager.get_giveaway(msg_id)
        if not giveaway:
            await interaction.followup.send(
                "Giveaway nicht gefunden.", ephemeral=True
            )
            return

        if not giveaway.get("ended"):
            await interaction.followup.send(
                "Dieses Giveaway ist noch aktiv. Nutze /giveaway end um es zu beenden.",
                ephemeral=True,
            )
            return

        # Reroll ausfuehren
        success, new_winners = self.manager.reroll(msg_id)

        if not success:
            await interaction.followup.send(
                "Reroll fehlgeschlagen.", ephemeral=True
            )
            return

        if not new_winners:
            await interaction.followup.send(
                "Keine weiteren Teilnehmer fuer einen Reroll verfuegbar.",
                ephemeral=True,
            )
            return

        # Gewinner im Kanal bekannt geben
        channel_id = giveaway.get("channel_id")
        prize = giveaway.get("prize", "Unbekannt")
        channel = self.bot.get_channel(channel_id)

        if channel:
            winners_mentions = ", ".join(f"<@{uid}>" for uid in new_winners)
            try:
                await channel.send(
                    f"\U0001f504 **Reroll!** Neue Gewinner: {winners_mentions}\n"
                    f"Herzlichen Glueckwunsch! Ihr habt **{prize}** gewonnen!"
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Reroll-Nachricht senden fehlgeschlagen: {e}")

            # Embed aktualisieren
            try:
                message = await channel.fetch_message(msg_id)
                participant_count = len(giveaway.get("participants", []))
                ended_embed = self._build_ended_embed(
                    prize, new_winners, participant_count
                )
                await message.edit(embed=ended_embed)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Reroll-Embed aktualisieren fehlgeschlagen: {e}")

            # Neue Gewinner per DM benachrichtigen
            for uid in new_winners:
                await self._notify_winner_dm(uid, prize, channel)

        winners_str = ", ".join(f"<@{uid}>" for uid in new_winners)
        await interaction.followup.send(
            f"Reroll erfolgreich! Neue Gewinner: {winners_str}",
            ephemeral=True,
        )

        logger.info(
            f"Giveaway Reroll: {msg_id} — Neue Gewinner: {new_winners} "
            f"(von {interaction.user})"
        )

    @giveaway_grp.command(
        name="cancel",
        description="Giveaway ohne Gewinner abbrechen (Admin)",
    )
    @app_commands.describe(
        message_id="Message-ID des Giveaway-Embeds",
    )
    @admin_only()
    async def giveaway_cancel(
        self,
        interaction: discord.Interaction,
        message_id: str,
    ) -> None:
        """Giveaway abbrechen"""
        await interaction.response.defer(ephemeral=True)

        try:
            msg_id = int(message_id)
        except ValueError:
            await interaction.followup.send(
                "Ungueltige Message-ID.", ephemeral=True
            )
            return

        giveaway = self.manager.get_giveaway(msg_id)
        if not giveaway:
            await interaction.followup.send(
                "Giveaway nicht gefunden.", ephemeral=True
            )
            return

        if giveaway.get("ended"):
            await interaction.followup.send(
                "Dieses Giveaway ist bereits beendet.", ephemeral=True
            )
            return

        prize = giveaway.get("prize", "Unbekannt")

        # Giveaway abbrechen
        success = self.manager.cancel(msg_id)
        if not success:
            await interaction.followup.send(
                "Abbrechen fehlgeschlagen.", ephemeral=True
            )
            return

        # Embed aktualisieren
        channel_id = giveaway.get("channel_id")
        channel = self.bot.get_channel(channel_id)

        if channel:
            try:
                message = await channel.fetch_message(msg_id)

                cancelled_embed = discord.Embed(
                    title="\U0001f389 Giveaway abgebrochen",
                    description=f"~~**{prize}**~~\n\nDieses Giveaway wurde abgebrochen.",
                    color=COLOR_CANCELLED,
                    timestamp=datetime.now(timezone.utc),
                )

                # View mit deaktiviertem Button
                disabled_view = discord.ui.View(timeout=None)
                disabled_button = discord.ui.Button(
                    label="Abgebrochen",
                    style=discord.ButtonStyle.grey,
                    emoji="\U0001f6ab",
                    disabled=True,
                    custom_id="giveaway_cancelled:disabled",
                )
                disabled_view.add_item(disabled_button)

                await message.edit(embed=cancelled_embed, view=disabled_view)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Cancel-Embed aktualisieren fehlgeschlagen: {e}")

        await interaction.followup.send(
            f"Giveaway **{prize}** wurde abgebrochen.", ephemeral=True
        )

        logger.info(
            f"Giveaway abgebrochen: '{prize}' (Message: {msg_id}, "
            f"von {interaction.user})"
        )

    @giveaway_grp.command(
        name="list",
        description="Alle aktiven Giveaways anzeigen (Admin)",
    )
    @admin_only()
    async def giveaway_list(self, interaction: discord.Interaction) -> None:
        """Aktive Giveaways auflisten"""
        await interaction.response.defer(ephemeral=True)

        active = self.manager.get_active()

        if not active:
            await interaction.followup.send(
                "Keine aktiven Giveaways.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"Aktive Giveaways ({len(active)})",
            color=COLOR_ACTIVE,
        )

        for giveaway in active[:25]:  # Max 25 Felder im Embed
            prize = giveaway.get("prize", "?")
            msg_id = giveaway.get("message_id", "?")
            channel_id = giveaway.get("channel_id", 0)
            participants = len(giveaway.get("participants", []))
            winners_count = giveaway.get("winners_count", 1)

            # Restzeit berechnen
            ends_at_str = giveaway.get("ends_at", "")
            try:
                ends_at = datetime.fromisoformat(ends_at_str)
                remaining = ends_at - datetime.now()
                if remaining.total_seconds() > 0:
                    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    if hours > 24:
                        days = hours // 24
                        hours = hours % 24
                        restzeit = f"{days}d {hours}h"
                    elif hours > 0:
                        restzeit = f"{hours}h {minutes}m"
                    else:
                        restzeit = f"{minutes}m"
                else:
                    restzeit = "Laeuft gleich ab..."
            except (ValueError, TypeError):
                restzeit = "?"

            embed.add_field(
                name=f"{prize}",
                value=(
                    f"**Kanal:** <#{channel_id}>\n"
                    f"**Message-ID:** `{msg_id}`\n"
                    f"**Teilnehmer:** {participants}\n"
                    f"**Gewinner:** {winners_count}\n"
                    f"**Restzeit:** {restzeit}"
                ),
                inline=False,
            )

        if len(active) > 25:
            embed.set_footer(text=f"... und {len(active) - 25} weitere")

        await interaction.followup.send(embed=embed, ephemeral=True)

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
    """Cog laden und Persistent View registrieren"""
    await bot.add_cog(GiveawayCog(bot))
