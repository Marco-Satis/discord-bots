"""
Welcome Cog — F41: Willkommenssystem für neue Discord-Mitglieder

Begruesst neue Mitglieder mit einem konfigurierbaren Embed im
festgelegten Kanal, weist optional eine Auto-Rolle zu und sendet
bei Bedarf eine DM mit Regeln/Infos.

Listener:
  on_member_join — Willkommens-Embed senden, Auto-Rolle zuweisen, DM senden

Command-Struktur (app_commands.Group, nur Admins):
  /welcome channel <channel> — Willkommens-Kanal festlegen
  /welcome role <role>       — Auto-Rolle festlegen
  /welcome message <text>    — Willkommensnachricht anpassen
  /welcome toggle            — System aktivieren/deaktivieren
  /welcome test              — Test-Willkommensnachricht senden

Variablen in Nachrichten:
  {user}        -> Mention des neuen Mitglieds
  {server}      -> Guild-Name
  {membercount} -> Aktuelle Mitgliederzahl

Persistenz: SQLite-Tabelle 'welcome_config'
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.database.db_manager import get_db
from utils.logger import get_logger
from utils.embeds import COLOR_INFO, COLOR_SUCCESS

logger = get_logger("cogs.welcome")

# SQL für Tabellenerstellung
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS welcome_config (
    guild_id TEXT PRIMARY KEY,
    channel_id TEXT,
    role_id TEXT,
    message TEXT DEFAULT 'Willkommen auf {server}, {user}! Du bist Mitglied #{membercount}.',
    dm_message TEXT,
    enabled INTEGER DEFAULT 1
)
"""


class WelcomeCog(commands.Cog):
    """Willkommenssystem: Begruessung, Auto-Rolle und DM für neue Mitglieder"""

    welcome_grp = app_commands.Group(
        name="welcome",
        description="Willkommenssystem — Begruessung neuer Mitglieder konfigurieren",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Cog Lifecycle
    # ------------------------------------------------------------------

    async def cog_load(self) -> None:
        """Tabelle erstellen falls nicht vorhanden und Cog initialisieren"""
        await self._ensure_table()
        logger.info("Welcome-Cog geladen")

    async def cog_unload(self) -> None:
        logger.info("Welcome-Cog entladen")

    # ------------------------------------------------------------------
    # Datenbank-Hilfsmethoden
    # ------------------------------------------------------------------

    async def _ensure_table(self) -> None:
        """Stellt sicher, dass die Tabelle 'welcome_config' existiert"""
        try:
            db = await get_db()
            await db.execute(CREATE_TABLE_SQL)
            await db.commit()
            logger.info("Tabelle 'welcome_config' sichergestellt")
        except Exception as e:
            logger.error(f"Fehler beim Erstellen der Tabelle: {e}", exc_info=True)

    async def _get_config(self, guild_id: str) -> Optional[dict]:
        """
        Willkommens-Konfiguration für eine Guild laden.

        Args:
            guild_id: Discord Guild ID als String

        Returns:
            Dict mit Konfigurationsdaten oder None wenn nicht vorhanden
        """
        db = await get_db()
        cursor = await db.execute(
            "SELECT guild_id, channel_id, role_id, message, dm_message, enabled "
            "FROM welcome_config WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "guild_id": row[0],
            "channel_id": row[1],
            "role_id": row[2],
            "message": row[3],
            "dm_message": row[4],
            "enabled": bool(row[5]),
        }

    async def _ensure_config(self, guild_id: str) -> dict:
        """
        Konfiguration laden oder mit Standardwerten erstellen.

        Stellt sicher, dass ein Eintrag für die Guild existiert.

        Args:
            guild_id: Discord Guild ID als String

        Returns:
            Dict mit Konfigurationsdaten
        """
        config = await self._get_config(guild_id)
        if config is not None:
            return config

        # Standard-Konfiguration erstellen
        try:
            db = await get_db()
            await db.execute(
                "INSERT OR IGNORE INTO welcome_config (guild_id) VALUES (?)",
                (guild_id,),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Fehler beim Erstellen der Standardkonfiguration: {e}", exc_info=True)

        # Neu laden (mit Defaults aus der Tabelle)
        config = await self._get_config(guild_id)
        if config is None:
            # Fallback falls DB-Fehler
            return {
                "guild_id": guild_id,
                "channel_id": None,
                "role_id": None,
                "message": "Willkommen auf {server}, {user}! Du bist Mitglied #{membercount}.",
                "dm_message": None,
                "enabled": True,
            }
        return config

    # ------------------------------------------------------------------
    # Variablen-Ersetzung
    # ------------------------------------------------------------------

    @staticmethod
    def _replace_variables(
        text: str,
        member: discord.Member,
    ) -> str:
        """
        Variablen im Nachrichtentext ersetzen.

        Unterstuetzte Variablen:
          {user}        -> Mention des Mitglieds
          {server}      -> Guild-Name
          {membercount} -> Aktuelle Mitgliederzahl

        Args:
            text: Roher Text mit Platzhaltern
            member: Das neue Discord-Mitglied

        Returns:
            Text mit ersetzten Variablen
        """
        replacements = {
            "{user}": member.mention,
            "{server}": member.guild.name,
            "{membercount}": str(member.guild.member_count),
        }
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        return text

    # ------------------------------------------------------------------
    # Willkommens-Embed erstellen
    # ------------------------------------------------------------------

    def _build_welcome_embed(
        self,
        member: discord.Member,
        message_text: str,
    ) -> discord.Embed:
        """
        Willkommens-Embed für ein neues Mitglied erstellen.

        Args:
            member: Das neue Mitglied
            message_text: Bereits ersetzte Willkommensnachricht

        Returns:
            Fertiges discord.Embed-Objekt
        """
        embed = discord.Embed(
            title=f"Willkommen, {member.display_name}!",
            description=message_text,
            color=COLOR_SUCCESS,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(
            text=f"Mitglied #{member.guild.member_count} | {member.guild.name}"
        )
        return embed

    # ==================================================================
    # Listener: on_member_join
    # ==================================================================

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """
        Neues Mitglied beigetreten: Willkommens-Embed senden,
        Auto-Rolle zuweisen, DM senden (falls konfiguriert).
        """
        # Bots ignorieren
        if member.bot:
            return

        guild_id = str(member.guild.id)

        try:
            config = await self._get_config(guild_id)
        except Exception as e:
            logger.error(
                f"Fehler beim Laden der Welcome-Config für Guild {guild_id}: {e}",
                exc_info=True,
            )
            return

        # Kein Eintrag oder deaktiviert
        if config is None or not config.get("enabled", False):
            return

        # --- 1. Willkommens-Embed im konfigurierten Kanal senden ---
        channel_id = config.get("channel_id")
        if channel_id:
            channel = member.guild.get_channel(int(channel_id))
            if channel is not None:
                try:
                    msg_text = self._replace_variables(
                        config.get("message", "Willkommen {user}!"),
                        member,
                    )
                    embed = self._build_welcome_embed(member, msg_text)
                    await channel.send(embed=embed)
                    logger.info(
                        f"Welcome: Embed gesendet für {member} in #{channel.name}"
                    )
                except discord.Forbidden:
                    logger.warning(
                        f"Welcome: Keine Berechtigung zum Senden in #{channel.name}"
                    )
                except discord.HTTPException as e:
                    logger.error(
                        f"Welcome: Fehler beim Senden des Embeds: {e}"
                    )
            else:
                logger.warning(
                    f"Welcome: Kanal {channel_id} nicht gefunden in Guild {guild_id}"
                )

        # --- 2. Auto-Rolle zuweisen ---
        role_id = config.get("role_id")
        if role_id:
            role = member.guild.get_role(int(role_id))
            if role is not None:
                try:
                    await member.add_roles(role, reason="Welcome Auto-Rolle")
                    logger.info(
                        f"Welcome: Auto-Rolle '{role.name}' zugewiesen an {member}"
                    )
                except discord.Forbidden:
                    logger.warning(
                        f"Welcome: Keine Berechtigung, Rolle '{role.name}' "
                        f"an {member} zuzuweisen"
                    )
                except discord.HTTPException as e:
                    logger.error(
                        f"Welcome: Fehler beim Zuweisen der Rolle: {e}"
                    )
            else:
                logger.warning(
                    f"Welcome: Rolle {role_id} nicht gefunden in Guild {guild_id}"
                )

        # --- 3. DM an das neue Mitglied senden (falls konfiguriert) ---
        dm_message = config.get("dm_message")
        if dm_message:
            try:
                dm_text = self._replace_variables(dm_message, member)
                dm_embed = discord.Embed(
                    title=f"Willkommen auf {member.guild.name}!",
                    description=dm_text,
                    color=COLOR_INFO,
                )
                dm_embed.set_thumbnail(url=member.guild.icon.url if member.guild.icon else "")
                await member.send(embed=dm_embed)
                logger.info(f"Welcome: DM gesendet an {member}")
            except discord.Forbidden:
                # User hat DMs deaktiviert — kein Fehler
                logger.debug(
                    f"Welcome: DMs deaktiviert für {member}, überspringe"
                )
            except discord.HTTPException as e:
                logger.warning(
                    f"Welcome: DM an {member} fehlgeschlagen: {e}"
                )

    # ==================================================================
    # /welcome channel <channel>
    # ==================================================================

    @welcome_grp.command(
        name="channel",
        description="Willkommens-Kanal festlegen (Admin)",
    )
    @app_commands.describe(
        channel="Kanal in dem Willkommensnachrichten gesendet werden",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_id = str(interaction.guild.id)
        await self._ensure_config(guild_id)

        try:
            db = await get_db()
            await db.execute(
                "UPDATE welcome_config SET channel_id = ? WHERE guild_id = ?",
                (str(channel.id), guild_id),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Fehler beim Setzen des Welcome-Channels: {e}", exc_info=True)
            await interaction.followup.send(
                f"Fehler beim Speichern: {e}", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Willkommens-Kanal gesetzt",
            description=f"Neue Mitglieder werden in {channel.mention} begruesst.",
            color=COLOR_SUCCESS,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"Welcome-Channel gesetzt: #{channel.name} von {interaction.user}"
        )

    # ==================================================================
    # /welcome role <role>
    # ==================================================================

    @welcome_grp.command(
        name="role",
        description="Auto-Rolle für neue Mitglieder festlegen (Admin)",
    )
    @app_commands.describe(
        role="Rolle die neuen Mitgliedern automatisch zugewiesen wird",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        # Pruefen ob der Bot die Rolle zuweisen kann
        bot_member = interaction.guild.me
        if role >= bot_member.top_role:
            await interaction.followup.send(
                f"Die Rolle **{role.name}** ist hoeher als meine hoechste Rolle. "
                f"Ich kann sie nicht zuweisen.",
                ephemeral=True,
            )
            return

        if role.is_default():
            await interaction.followup.send(
                "Die @everyone-Rolle kann nicht als Auto-Rolle verwendet werden.",
                ephemeral=True,
            )
            return

        guild_id = str(interaction.guild.id)
        await self._ensure_config(guild_id)

        try:
            db = await get_db()
            await db.execute(
                "UPDATE welcome_config SET role_id = ? WHERE guild_id = ?",
                (str(role.id), guild_id),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Fehler beim Setzen der Auto-Rolle: {e}", exc_info=True)
            await interaction.followup.send(
                f"Fehler beim Speichern: {e}", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Auto-Rolle gesetzt",
            description=f"Neue Mitglieder erhalten automatisch die Rolle **{role.name}**.",
            color=COLOR_SUCCESS,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"Welcome-Rolle gesetzt: {role.name} von {interaction.user}"
        )

    # ==================================================================
    # /welcome message <text>
    # ==================================================================

    @welcome_grp.command(
        name="message",
        description="Willkommensnachricht anpassen (Admin)",
    )
    @app_commands.describe(
        text="Nachrichtentext (Variablen: {user}, {server}, {membercount})",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_message(
        self,
        interaction: discord.Interaction,
        text: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_id = str(interaction.guild.id)
        await self._ensure_config(guild_id)

        try:
            db = await get_db()
            await db.execute(
                "UPDATE welcome_config SET message = ? WHERE guild_id = ?",
                (text, guild_id),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Fehler beim Setzen der Willkommensnachricht: {e}", exc_info=True)
            await interaction.followup.send(
                f"Fehler beim Speichern: {e}", ephemeral=True
            )
            return

        # Vorschau der Nachricht
        preview = text.replace("{user}", interaction.user.mention)
        preview = preview.replace("{server}", interaction.guild.name)
        preview = preview.replace("{membercount}", str(interaction.guild.member_count))

        embed = discord.Embed(
            title="Willkommensnachricht gesetzt",
            color=COLOR_SUCCESS,
        )
        embed.add_field(name="Vorlage", value=f"```{text}```", inline=False)
        embed.add_field(name="Vorschau", value=preview, inline=False)
        embed.set_footer(
            text="Variablen: {user} = Mention, {server} = Servername, {membercount} = Mitgliederzahl"
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"Welcome-Message gesetzt von {interaction.user}"
        )

    # ==================================================================
    # /welcome toggle
    # ==================================================================

    @welcome_grp.command(
        name="toggle",
        description="Willkommenssystem aktivieren/deaktivieren (Admin)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_toggle(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_id = str(interaction.guild.id)
        config = await self._ensure_config(guild_id)

        # Status umschalten
        new_enabled = not config.get("enabled", True)

        try:
            db = await get_db()
            await db.execute(
                "UPDATE welcome_config SET enabled = ? WHERE guild_id = ?",
                (1 if new_enabled else 0, guild_id),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Fehler beim Toggle des Welcome-Systems: {e}", exc_info=True)
            await interaction.followup.send(
                f"Fehler beim Speichern: {e}", ephemeral=True
            )
            return

        status_text = "aktiviert" if new_enabled else "deaktiviert"
        status_color = COLOR_SUCCESS if new_enabled else 0xE74C3C

        embed = discord.Embed(
            title="Willkommenssystem",
            description=f"Das Willkommenssystem wurde **{status_text}**.",
            color=status_color,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"Welcome-System {status_text} von {interaction.user}"
        )

    # ==================================================================
    # /welcome test
    # ==================================================================

    @welcome_grp.command(
        name="test",
        description="Test-Willkommensnachricht senden (Admin)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_test(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_id = str(interaction.guild.id)
        config = await self._get_config(guild_id)

        if config is None:
            await interaction.followup.send(
                "Kein Willkommenssystem konfiguriert.\n"
                "Richte zuerst einen Kanal ein mit `/welcome channel <channel>`.",
                ephemeral=True,
            )
            return

        # Status-Übersicht anzeigen
        status_lines: list[str] = []
        status_lines.append(
            f"**Status:** {'Aktiviert' if config.get('enabled') else 'Deaktiviert'}"
        )

        channel_id = config.get("channel_id")
        if channel_id:
            channel = interaction.guild.get_channel(int(channel_id))
            status_lines.append(
                f"**Kanal:** {channel.mention if channel else f'ID {channel_id} (nicht gefunden)'}"
            )
        else:
            status_lines.append("**Kanal:** Nicht gesetzt")

        role_id = config.get("role_id")
        if role_id:
            role = interaction.guild.get_role(int(role_id))
            status_lines.append(
                f"**Auto-Rolle:** {role.mention if role else f'ID {role_id} (nicht gefunden)'}"
            )
        else:
            status_lines.append("**Auto-Rolle:** Nicht gesetzt")

        dm_msg = config.get("dm_message")
        status_lines.append(
            f"**DM-Nachricht:** {'Konfiguriert' if dm_msg else 'Nicht gesetzt'}"
        )

        # Status-Embed
        status_embed = discord.Embed(
            title="Welcome-System Konfiguration",
            description="\n".join(status_lines),
            color=COLOR_INFO,
        )
        await interaction.followup.send(embed=status_embed, ephemeral=True)

        # Test-Willkommensnachricht im konfigurierten Kanal senden
        if channel_id:
            channel = interaction.guild.get_channel(int(channel_id))
            if channel is not None:
                try:
                    msg_text = self._replace_variables(
                        config.get("message", "Willkommen {user}!"),
                        interaction.user,
                    )
                    embed = self._build_welcome_embed(interaction.user, msg_text)
                    embed.set_footer(text="Dies ist eine Test-Nachricht")
                    await channel.send(embed=embed)

                    await interaction.followup.send(
                        f"Test-Nachricht wurde in {channel.mention} gesendet.",
                        ephemeral=True,
                    )
                except discord.Forbidden:
                    await interaction.followup.send(
                        f"Keine Berechtigung zum Senden in {channel.mention}.",
                        ephemeral=True,
                    )
                except discord.HTTPException as e:
                    await interaction.followup.send(
                        f"Fehler beim Senden der Test-Nachricht: {e}",
                        ephemeral=True,
                    )
            else:
                await interaction.followup.send(
                    f"Kanal mit ID {channel_id} wurde nicht gefunden.",
                    ephemeral=True,
                )

        logger.info(f"Welcome-Test ausgefuehrt von {interaction.user}")

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung für alle Commands in dieser Cog"""
        if isinstance(error, app_commands.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Du benoetigst Administrator-Rechte für diesen Befehl.",
                    ephemeral=True,
                )
            return

        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung für diesen Befehl.", ephemeral=True
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
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")


async def setup(bot: commands.Bot) -> None:
    """Cog laden"""
    await bot.add_cog(WelcomeCog(bot))
