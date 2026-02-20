"""
Ticket Cog — Support-Ticket-System fuer den Admin Bot

Features:
  - Support-Embed mit interaktivem "Ticket erstellen" Button
  - Private Ticket-Channels mit individuellen Berechtigungen
  - Automatisches Transcript-Logging aller Nachrichten
  - Ticket schliessen mit Transcript-Export in Log-Channel
  - Persistente Views (ueberleben Bot-Neustarts)

Command-Struktur:
  /ticket setup <channel>    — Support-Embed posten (Admin)
  /ticket close [grund]      — Aktuelles Ticket schliessen (im Ticket-Channel)
  /ticket list               — Alle offenen Tickets anzeigen (Admin)

Interaktive Elemente:
  - "Ticket erstellen" Button (persistent, custom_id basiert)
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.tickets import TicketManager
from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR
from utils.permissions import admin_only

logger = get_logger("cogs.tickets")

# Maximale offene Tickets pro User (Spam-Schutz)
MAX_OPEN_TICKETS_PER_USER = 3


# ======================================================================
# Persistente Views — ueberleben Bot-Neustarts
# ======================================================================

class TicketCreateView(discord.ui.View):
    """
    Persistente View mit dem "Ticket erstellen" Button.

    Verwendet einen festen custom_id damit der Button auch nach
    einem Bot-Neustart funktioniert. Die View wird in setup()
    beim Bot registriert.
    """

    def __init__(self) -> None:
        # timeout=None fuer persistente Views (kein Ablauf)
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ticket erstellen",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_system:create_ticket",
        emoji="\U0001f3ab",
    )
    async def create_ticket_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Button-Handler: Neues Ticket erstellen."""
        # Modal fuer Ticket-Betreff anzeigen
        modal = TicketCreateModal()
        await interaction.response.send_modal(modal)


class TicketCreateModal(discord.ui.Modal, title="Neues Support-Ticket"):
    """
    Modal-Dialog fuer die Ticket-Erstellung.

    Fragt den Betreff / die Beschreibung des Problems ab.
    """

    subject = discord.ui.TextInput(
        label="Betreff",
        placeholder="Beschreibe dein Anliegen kurz...",
        style=discord.TextStyle.short,
        max_length=100,
        required=True,
    )

    description = discord.ui.TextInput(
        label="Beschreibung",
        placeholder="Erklaere dein Problem ausfuehrlicher...",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Modal abgeschickt — Ticket wird erstellt."""
        # Cog-Referenz ueber den Bot holen
        cog: TicketsCog | None = interaction.client.get_cog("TicketsCog")
        if not cog:
            await interaction.response.send_message(
                "Ticket-System ist nicht verfuegbar.", ephemeral=True
            )
            return

        await cog.handle_ticket_creation(
            interaction,
            subject=self.subject.value,
            description=self.description.value or None,
        )


class TicketCloseView(discord.ui.View):
    """
    Persistente View mit dem "Ticket schliessen" Button.

    Wird in jedem Ticket-Channel als Willkommensnachricht angezeigt.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ticket schliessen",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_system:close_ticket",
        emoji="\U0001f512",
    )
    async def close_ticket_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Button-Handler: Ticket schliessen."""
        cog: TicketsCog | None = interaction.client.get_cog("TicketsCog")
        if not cog:
            await interaction.response.send_message(
                "Ticket-System ist nicht verfuegbar.", ephemeral=True
            )
            return

        await cog.handle_ticket_close(interaction, reason=None)


# ======================================================================
# Ticket Cog
# ======================================================================

class TicketsCog(commands.Cog):
    """Support-Ticket-System mit interaktiven Buttons und Transcripts"""

    # Slash-Command-Gruppe
    ticket_grp = app_commands.Group(
        name="ticket",
        description="Support-Ticket-System",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.ticket_mgr = TicketManager(
            data_file=ADMIN_DATA_DIR / "tickets.json",
            config_file=ADMIN_DATA_DIR / "ticket_config.json",
        )

    async def cog_load(self) -> None:
        """Persistente Views beim Laden registrieren."""
        # Views muessen beim Bot registriert werden damit sie
        # nach einem Neustart wieder funktionieren
        self.bot.add_view(TicketCreateView())
        self.bot.add_view(TicketCloseView())
        logger.info("Ticket-Cog geladen, persistente Views registriert")

    # ==================================================================
    # Event: Nachrichten in Ticket-Channels tracken
    # ==================================================================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        Nachrichten in Ticket-Channels zum Transcript hinzufuegen.

        Ueberspringt Bot-Nachrichten und DMs.
        """
        if message.author.bot:
            return
        if not message.guild:
            return

        # Pruefen ob die Nachricht in einem Ticket-Channel ist
        ticket = self.ticket_mgr.get_ticket_by_channel(message.channel.id)
        if not ticket:
            return
        if ticket.get("status") != "open":
            return

        # Nachricht zum Transcript hinzufuegen
        content = message.content or ""

        # Anhaenge als Text anhaengen
        if message.attachments:
            attachment_urls = [a.url for a in message.attachments]
            if content:
                content += "\n"
            content += "[Anhaenge: " + ", ".join(attachment_urls) + "]"

        if content:
            self.ticket_mgr.add_transcript_entry(
                ticket["ticket_id"],
                author=message.author.display_name,
                content=content,
                author_id=message.author.id,
            )

    # ==================================================================
    # Ticket-Erstellung (von Modal aufgerufen)
    # ==================================================================

    async def handle_ticket_creation(
        self,
        interaction: discord.Interaction,
        subject: str,
        description: str | None = None,
    ) -> None:
        """
        Ticket erstellen: Channel anlegen, Berechtigungen setzen, Willkommen senden.

        Args:
            interaction: Discord-Interaction (vom Modal)
            subject: Ticket-Betreff
            description: Optionale ausfuehrliche Beschreibung
        """
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild:
            await interaction.followup.send(
                "Tickets koennen nur auf einem Server erstellt werden.",
                ephemeral=True,
            )
            return

        user = interaction.user

        # Spam-Schutz: Maximale Anzahl offener Tickets pruefen
        open_count = self.ticket_mgr.get_user_ticket_count(user.id)
        if open_count >= MAX_OPEN_TICKETS_PER_USER:
            await interaction.followup.send(
                f"Du hast bereits {open_count} offene Tickets. "
                f"Bitte schliesse ein Ticket bevor du ein neues erstellst.",
                ephemeral=True,
            )
            return

        # Ticket im Manager erstellen
        ticket = self.ticket_mgr.create_ticket(
            user_id=user.id,
            subject=subject,
        )
        ticket_id = ticket["ticket_id"]

        # Kategorie fuer Ticket-Channels ermitteln
        category = None
        cat_id = self.ticket_mgr.ticket_category_id
        if cat_id:
            category = guild.get_channel(cat_id)
            if category and not isinstance(category, discord.CategoryChannel):
                category = None

        # Berechtigungen setzen
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
            ),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            ),
        }

        # Support-Rollen Zugriff geben
        for role_id in self.ticket_mgr.support_roles:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                )

        # Channel erstellen
        channel_name = f"ticket-{ticket_id:04d}"
        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Ticket #{ticket_id} — {subject} — von {user.display_name}",
                reason=f"Support-Ticket #{ticket_id} von {user.display_name}",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Ticket-Channel erstellen fehlgeschlagen: {e}")
            await interaction.followup.send(
                "Ticket-Channel konnte nicht erstellt werden. "
                "Bitte kontaktiere einen Admin.",
                ephemeral=True,
            )
            return

        # Channel-ID im Ticket speichern
        self.ticket_mgr.update_ticket_channel(ticket_id, channel.id)

        # Willkommens-Embed senden
        embed = discord.Embed(
            title=f"Ticket #{ticket_id}",
            description=(
                f"**Betreff:** {subject}\n\n"
                f"Willkommen in deinem Support-Ticket, {user.mention}!\n"
                f"Ein Teammitglied wird sich in Kuerze um dein Anliegen kuemmern.\n\n"
                f"Beschreibe dein Problem so detailliert wie moeglich."
            ),
            color=0x5865F2,
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"Ticket erstellt von {user.display_name}")

        if description:
            embed.add_field(
                name="Beschreibung",
                value=description,
                inline=False,
            )

        # Willkommen-Nachricht mit Schliessen-Button senden
        close_view = TicketCloseView()
        try:
            await channel.send(embed=embed, view=close_view)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Willkommens-Embed senden fehlgeschlagen: {e}")

        # Beschreibung als ersten Transcript-Eintrag speichern
        if description:
            self.ticket_mgr.add_transcript_entry(
                ticket_id,
                author=user.display_name,
                content=description,
                author_id=user.id,
            )

        # Support-Rollen erwaehnen (in separater Nachricht, damit Embed sauber bleibt)
        support_mentions: list[str] = []
        for role_id in self.ticket_mgr.support_roles:
            role = guild.get_role(role_id)
            if role:
                support_mentions.append(role.mention)

        if support_mentions:
            try:
                ping_msg = await channel.send(
                    f"Neues Ticket von {user.mention} — "
                    + " ".join(support_mentions)
                )
                # Ping-Nachricht nach kurzer Zeit loeschen (optisch sauberer)
                await asyncio.sleep(2)
                try:
                    await ping_msg.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass
            except (discord.Forbidden, discord.HTTPException):
                pass

        # Bestaetigung an den User
        await interaction.followup.send(
            f"Dein Ticket wurde erstellt: {channel.mention}",
            ephemeral=True,
        )

        logger.info(
            f"Ticket #{ticket_id} erstellt: {channel_name} "
            f"von {user.display_name} — {subject}"
        )

    # ==================================================================
    # Ticket schliessen (von Button oder Command aufgerufen)
    # ==================================================================

    async def handle_ticket_close(
        self,
        interaction: discord.Interaction,
        reason: str | None = None,
    ) -> None:
        """
        Ticket schliessen: Transcript sichern, Log senden, Channel loeschen.

        Args:
            interaction: Discord-Interaction
            reason: Optionaler Grund fuer das Schliessen
        """
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.",
                ephemeral=True,
            )
            return

        # Pruefen ob wir in einem Ticket-Channel sind
        ticket = self.ticket_mgr.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            await interaction.followup.send(
                "Dieser Channel ist kein Ticket-Channel.",
                ephemeral=True,
            )
            return

        if ticket.get("status") == "closed":
            await interaction.followup.send(
                "Dieses Ticket ist bereits geschlossen.",
                ephemeral=True,
            )
            return

        ticket_id = ticket["ticket_id"]
        closed_by = interaction.user

        # Ticket im Manager schliessen
        closed_ticket = self.ticket_mgr.close_ticket(
            ticket_id,
            closed_by=closed_by.id,
            reason=reason,
        )

        if not closed_ticket:
            await interaction.followup.send(
                "Ticket konnte nicht geschlossen werden.",
                ephemeral=True,
            )
            return

        # Transcript formatieren
        transcript_text = self.ticket_mgr.format_transcript(ticket_id)

        # Transcript an Log-Channel senden
        await self._send_transcript_log(
            interaction.guild,
            closed_ticket,
            transcript_text,
            closed_by,
        )

        # Bestaetigung im Ticket-Channel
        embed = discord.Embed(
            title="Ticket geschlossen",
            description=(
                f"Dieses Ticket wurde von {closed_by.mention} geschlossen."
                + (f"\n**Grund:** {reason}" if reason else "")
                + "\n\nDer Channel wird in 5 Sekunden geloescht."
            ),
            color=0xe74c3c,
            timestamp=datetime.now(),
        )

        try:
            await interaction.channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

        await interaction.followup.send(
            "Ticket wird geschlossen...", ephemeral=True
        )

        # Channel nach Verzoegerung loeschen
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(
                reason=f"Ticket #{ticket_id} geschlossen von {closed_by.display_name}"
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(
                f"Ticket-Channel loeschen fehlgeschlagen (#{ticket_id}): {e}"
            )

        logger.info(
            f"Ticket #{ticket_id} geschlossen von {closed_by.display_name}"
            + (f" — Grund: {reason}" if reason else "")
        )

    async def _send_transcript_log(
        self,
        guild: discord.Guild,
        ticket: dict,
        transcript_text: str,
        closed_by: discord.Member | discord.User,
    ) -> None:
        """
        Transcript als Embed + Textdatei an den Log-Channel senden.

        Args:
            guild: Discord-Guild
            ticket: Ticket-Datensatz
            transcript_text: Formatierter Transcript-Text
            closed_by: User der das Ticket geschlossen hat
        """
        log_channel_id = self.ticket_mgr.log_channel_id
        if not log_channel_id:
            return

        log_channel = guild.get_channel(log_channel_id)
        if not log_channel or not isinstance(log_channel, discord.TextChannel):
            logger.warning(
                f"Log-Channel {log_channel_id} nicht gefunden oder kein Text-Channel"
            )
            return

        ticket_id = ticket.get("ticket_id", "?")
        user_id = ticket.get("user_id", 0)
        subject = ticket.get("subject", "Kein Betreff")
        created_at = ticket.get("created_at", "?")
        transcript_entries = ticket.get("transcript", [])

        # Erstelldatum formatieren
        try:
            dt = datetime.fromisoformat(created_at)
            created_at = dt.strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            pass

        # Log-Embed
        embed = discord.Embed(
            title=f"Ticket #{ticket_id} — Transcript",
            color=0x95a5a6,
            timestamp=datetime.now(),
        )
        embed.add_field(name="Betreff", value=subject, inline=True)
        embed.add_field(
            name="Erstellt von",
            value=f"<@{user_id}>",
            inline=True,
        )
        embed.add_field(name="Erstellt am", value=created_at, inline=True)
        embed.add_field(
            name="Geschlossen von",
            value=closed_by.mention,
            inline=True,
        )
        embed.add_field(
            name="Nachrichten",
            value=str(len(transcript_entries)),
            inline=True,
        )

        # Transcript als Datei anhaengen
        transcript_file = discord.File(
            fp=transcript_text.encode("utf-8"),
            filename=f"ticket-{ticket_id}-transcript.txt",
        )

        try:
            await log_channel.send(embed=embed, file=transcript_file)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Transcript-Log senden fehlgeschlagen: {e}")

    # ==================================================================
    # /ticket setup <channel>
    # ==================================================================

    @ticket_grp.command(
        name="setup",
        description="Support-Embed mit Ticket-Button in einem Channel posten",
    )
    @app_commands.describe(
        channel="Channel in dem das Support-Embed gepostet wird",
        kategorie="Kategorie fuer neue Ticket-Channels (optional)",
        log_channel="Channel fuer Transcript-Logs (optional)",
        support_rolle="Support-Rolle die Tickets sehen kann (optional)",
    )
    @admin_only()
    async def ticket_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        kategorie: Optional[discord.CategoryChannel] = None,
        log_channel: Optional[discord.TextChannel] = None,
        support_rolle: Optional[discord.Role] = None,
    ) -> None:
        """Support-Embed mit interaktivem Button posten und Konfiguration speichern."""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.",
                ephemeral=True,
            )
            return

        # Konfiguration aktualisieren
        config_updates: dict = {}

        if kategorie:
            config_updates["ticket_category_id"] = kategorie.id
        if log_channel:
            config_updates["log_channel_id"] = log_channel.id
        if support_rolle:
            # Bestehende Rollen beibehalten und neue hinzufuegen
            existing_roles = self.ticket_mgr.support_roles
            if support_rolle.id not in existing_roles:
                existing_roles.append(support_rolle.id)
            config_updates["support_roles"] = existing_roles

        if config_updates:
            self.ticket_mgr.set_config(**config_updates)

        # Support-Embed erstellen
        embed = discord.Embed(
            title="Support-Tickets",
            description=(
                "Brauchst du Hilfe oder hast ein Anliegen?\n\n"
                "Klicke auf den Button unten um ein Support-Ticket zu erstellen.\n"
                "Ein Teammitglied wird sich so schnell wie moeglich um dich kuemmern.\n\n"
                "**Bitte erstelle fuer jedes Anliegen ein eigenes Ticket.**"
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="Support-Ticket-System")

        # Persistente View erstellen
        view = TicketCreateView()

        try:
            await channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(
                f"Support-Embed konnte nicht gesendet werden: {e}",
                ephemeral=True,
            )
            return

        # Bestaetigung
        config_info: list[str] = [f"Support-Embed in {channel.mention} gepostet"]
        if kategorie:
            config_info.append(f"Ticket-Kategorie: {kategorie.name}")
        if log_channel:
            config_info.append(f"Log-Channel: {log_channel.mention}")
        if support_rolle:
            config_info.append(f"Support-Rolle: {support_rolle.name}")

        await interaction.followup.send(
            "\n".join(f"• {info}" for info in config_info),
            ephemeral=True,
        )

        logger.info(
            f"Ticket-Setup in #{channel.name} von {interaction.user.display_name}"
        )

    # ==================================================================
    # /ticket close [grund]
    # ==================================================================

    @ticket_grp.command(
        name="close",
        description="Aktuelles Ticket schliessen (nur in Ticket-Channels)",
    )
    @app_commands.describe(
        grund="Optionaler Grund fuer das Schliessen"
    )
    async def ticket_close(
        self,
        interaction: discord.Interaction,
        grund: Optional[str] = None,
    ) -> None:
        """Aktuelles Ticket schliessen (muss in einem Ticket-Channel ausgefuehrt werden)."""
        await self.handle_ticket_close(interaction, reason=grund)

    # ==================================================================
    # /ticket list
    # ==================================================================

    @ticket_grp.command(
        name="list",
        description="Alle offenen Tickets anzeigen",
    )
    @admin_only()
    async def ticket_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Alle offenen Tickets als Embed anzeigen."""
        await interaction.response.defer(ephemeral=True)

        open_tickets = self.ticket_mgr.get_open_tickets()

        if not open_tickets:
            await interaction.followup.send(
                "Keine offenen Tickets vorhanden.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Offene Tickets ({len(open_tickets)})",
            color=0x5865F2,
        )

        for ticket in open_tickets[:20]:  # Maximal 20 im Embed
            ticket_id = ticket.get("ticket_id", "?")
            user_id = ticket.get("user_id", 0)
            subject = ticket.get("subject", "Kein Betreff")
            channel_id = ticket.get("channel_id", 0)
            created_at = ticket.get("created_at", "?")
            transcript_count = len(ticket.get("transcript", []))

            # Datum formatieren
            try:
                dt = datetime.fromisoformat(created_at)
                created_at = dt.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                pass

            # Channel-Mention oder Fallback
            channel_ref = f"<#{channel_id}>" if channel_id else "Kein Channel"

            embed.add_field(
                name=f"Ticket #{ticket_id} — {subject}",
                value=(
                    f"**Erstellt von:** <@{user_id}>\n"
                    f"**Channel:** {channel_ref}\n"
                    f"**Erstellt am:** {created_at}\n"
                    f"**Nachrichten:** {transcript_count}"
                ),
                inline=False,
            )

        if len(open_tickets) > 20:
            embed.set_footer(
                text=f"... und {len(open_tickets) - 20} weitere Tickets"
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung fuer alle Commands in dieser Cog."""
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
    """Cog zum Bot hinzufuegen und persistente Views registrieren."""
    await bot.add_cog(TicketsCog(bot))
