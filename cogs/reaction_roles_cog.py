"""
Reaction Roles Cog — Reaction-Roles für den Admin Bot

Ermöglicht das Zuweisen von Rollen per Emoji-Reaktion auf Nachrichten.
Admins erstellen ein Embed, fuegen Emoji-Rollen-Paare hinzu, und User
können sich Rollen durch Reagieren selbst zuweisen/entfernen.

Command-Struktur (app_commands.Group):
  /reactionrole create <channel> <titel> <beschreibung>  — Embed erstellen
  /reactionrole add <message_id> <emoji> <rolle>         — Emoji-Rolle hinzufuegen
  /reactionrole remove <message_id> <emoji>              — Emoji-Rolle entfernen
  /reactionrole list                                     — Alle registrierten Nachrichten

Listener:
  on_raw_reaction_add    — Rolle zuweisen bei Reaktion
  on_raw_reaction_remove — Rolle entfernen bei Reaktionsentfernung

Persistenz: SQLite reaction_roles Tabelle (alleinige Datenquelle)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.logger import get_logger
from utils.permissions import admin_only
from modules.database.db_manager import get_db

logger = get_logger("cogs.reaction_roles")


class ReactionRolesCog(commands.Cog):
    """Reaction-Roles: Rollen per Emoji-Reaktion zuweisen/entfernen"""

    rr_grp = app_commands.Group(
        name="reactionrole",
        description="Reaction Roles — Rollen per Emoji verwalten",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # In-Memory Cache: {msg_id_str: {channel_id, guild_id, roles: {emoji: role_id}}}
        self._data: dict[str, dict[str, Any]] = {}
        # Race-Lock: per-message_id asyncio.Lock fuer Embed-Rebuild
        # (concurrent Reactions koennen sonst Lost-Updates verursachen).
        self._panel_locks: dict[int, asyncio.Lock] = {}
        self._panel_lock_use: dict[int, float] = {}

    def _get_panel_lock(self, message_id: int) -> asyncio.Lock:
        """Holt oder erstellt einen Lock fuer eine bestimmte panel-message_id."""
        if message_id not in self._panel_locks:
            self._panel_locks[message_id] = asyncio.Lock()
        self._panel_lock_use[message_id] = time.time()
        return self._panel_locks[message_id]

    def cleanup_old_panel_locks(self, max_age_hours: float = 24.0) -> int:
        """Entfernt Panel-Locks aelter als max_age_hours."""
        cutoff = time.time() - max_age_hours * 3600.0
        stale = [mid for mid, t in self._panel_lock_use.items() if t < cutoff]
        for mid in stale:
            self._panel_locks.pop(mid, None)
            self._panel_lock_use.pop(mid, None)
        return len(stale)

    async def cog_load(self) -> None:
        """Beim Laden des Cogs Daten aus SQLite laden und re-registrieren"""
        await self._load_from_db()
        self.bot.loop.create_task(self._re_register_reactions())
        self._cleanup_panel_locks_task.start()
        logger.info("Reaction-Roles-Cog geladen")

    async def cog_unload(self) -> None:
        self._cleanup_panel_locks_task.cancel()
        logger.info("Reaction-Roles-Cog entladen")

    @tasks.loop(hours=6)
    async def _cleanup_panel_locks_task(self) -> None:
        """Entfernt alle 6h Panel-Locks aelter als 24h (Memory-Hygiene)."""
        try:
            removed = self.cleanup_old_panel_locks(max_age_hours=24.0)
            if removed:
                logger.info(f"ReactionRoles: {removed} stale Panel-Locks entfernt")
        except Exception as e:
            logger.warning(f"Panel-Locks-Cleanup fehlgeschlagen: {e}")

    @_cleanup_panel_locks_task.before_loop
    async def before_cleanup_panel_locks(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Persistenz (SQLite)
    # ------------------------------------------------------------------

    async def _load_from_db(self) -> None:
        """Reaction-Role-Daten aus SQLite laden"""
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT message_id, channel_id, guild_id, emoji, role_id "
                "FROM reaction_roles ORDER BY message_id"
            )
            rows = await cursor.fetchall()

            self._data = {}
            for row in rows:
                msg_id = str(row[0])
                if msg_id not in self._data:
                    self._data[msg_id] = {
                        "channel_id": int(row[1]),
                        "guild_id": int(row[2]),
                        "roles": {},
                    }
                self._data[msg_id]["roles"][str(row[3])] = int(row[4])

            logger.info(
                f"Reaction-Roles aus SQLite geladen: {len(self._data)} Nachricht(en)"
            )
        except Exception as e:
            logger.error(f"Reaction-Roles SQLite-Load fehlgeschlagen: {e}")
            self._data = {}

    async def _db_add_role(self, message_id: str, channel_id: int,
                            guild_id: int, emoji: str, role_id: int) -> None:
        """Emoji-Rollen-Paar in SQLite speichern"""
        try:
            db = await get_db()
            await db.execute(
                "INSERT OR REPLACE INTO reaction_roles "
                "(message_id, channel_id, guild_id, emoji, role_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (message_id, str(channel_id), str(guild_id), emoji, str(role_id))
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Reaction-Roles SQLite-Write fehlgeschlagen: {e}")

    async def _db_remove_role(self, message_id: str, emoji: str) -> None:
        """Emoji-Rollen-Paar aus SQLite entfernen"""
        try:
            db = await get_db()
            await db.execute(
                "DELETE FROM reaction_roles WHERE message_id = ? AND emoji = ?",
                (message_id, emoji)
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Reaction-Roles SQLite-Delete fehlgeschlagen: {e}")

    async def _db_remove_message(self, message_id: str) -> None:
        """Alle Eintraege für eine Nachricht aus SQLite entfernen"""
        try:
            db = await get_db()
            await db.execute(
                "DELETE FROM reaction_roles WHERE message_id = ?",
                (message_id,)
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Reaction-Roles SQLite-Delete fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Re-Registrierung beim Start
    # ------------------------------------------------------------------

    async def _re_register_reactions(self) -> None:
        """
        Bestehende Reaction-Role-Nachrichten re-registrieren.

        Fuegt die Bot-Reaktionen erneut hinzu, falls sie fehlen.
        Entfernt Eintraege für Nachrichten/Channels die nicht mehr existieren.
        """
        await self.bot.wait_until_ready()

        if not self._data:
            return

        to_remove: list[str] = []

        for msg_id_str, entry in self._data.items():
            channel_id = entry.get("channel_id")
            roles_map = entry.get("roles", {})

            if not channel_id or not roles_map:
                to_remove.append(msg_id_str)
                continue

            # Channel holen
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logger.warning(
                        f"Reaction-Roles: Channel {channel_id} nicht gefunden, "
                        f"entferne Eintrag für Nachricht {msg_id_str}"
                    )
                    to_remove.append(msg_id_str)
                    continue

            # Nachricht holen
            try:
                message = await channel.fetch_message(int(msg_id_str))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning(
                    f"Reaction-Roles: Nachricht {msg_id_str} nicht gefunden, "
                    f"entferne Eintrag"
                )
                to_remove.append(msg_id_str)
                continue

            # Bot-Reaktionen hinzufuegen falls fehlend
            existing_reactions = {
                str(r.emoji): r for r in message.reactions
            }
            for emoji_str in roles_map:
                if emoji_str not in existing_reactions:
                    try:
                        await message.add_reaction(emoji_str)
                    except (discord.HTTPException, discord.NotFound) as e:
                        logger.warning(
                            f"Konnte Reaktion {emoji_str} nicht hinzufuegen "
                            f"zu Nachricht {msg_id_str}: {e}"
                        )

        # Ungueltige Eintraege entfernen
        if to_remove:
            for msg_id_str in to_remove:
                del self._data[msg_id_str]
                await self._db_remove_message(msg_id_str)
            logger.info(
                f"Reaction-Roles: {len(to_remove)} ungueltige Eintraege entfernt"
            )

        logger.info(
            f"Reaction-Roles re-registriert: {len(self._data)} Nachricht(en) aktiv"
        )

    # ==================================================================
    # /reactionrole create <channel> <titel> <beschreibung>
    # ==================================================================

    @rr_grp.command(
        name="create",
        description="Reaction-Role-Embed in einem Channel erstellen",
    )
    @app_commands.describe(
        channel="Channel in dem das Embed erstellt wird",
        titel="Titel des Embeds",
        beschreibung="Beschreibung des Embeds (Anleitung für User)",
    )
    @admin_only()
    async def rr_create(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        titel: str,
        beschreibung: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        # Berechtigungspruefung für den Ziel-Channel
        bot_member = interaction.guild.me
        perms = channel.permissions_for(bot_member)
        if not perms.send_messages or not perms.add_reactions:
            await interaction.followup.send(
                f"Ich habe keine Berechtigung zum Senden/Reagieren in {channel.mention}.",
                ephemeral=True,
            )
            return

        # Embed erstellen und senden
        embed = discord.Embed(
            title=titel,
            description=beschreibung,
            color=0x5865F2,
        )
        embed.set_footer(
            text="Reagiere mit einem Emoji um dir eine Rolle zuzuweisen!"
        )

        try:
            sent_message = await channel.send(embed=embed)
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"Konnte Embed nicht senden: {e}", ephemeral=True
            )
            return

        # Eintrag in Daten speichern (noch keine Rollen zugewiesen)
        msg_id_str = str(sent_message.id)
        self._data[msg_id_str] = {
            "channel_id": channel.id,
            "guild_id": interaction.guild.id,
            "roles": {},
        }

        await interaction.followup.send(
            f"Reaction-Role-Embed erstellt in {channel.mention}!\n"
            f"**Nachrichten-ID:** `{sent_message.id}`\n\n"
            f"Fuege jetzt Emoji-Rollen-Paare hinzu mit:\n"
            f"`/reactionrole add {sent_message.id} <emoji> <rolle>`",
            ephemeral=True,
        )

        logger.info(
            f"Reaction-Role-Embed erstellt: Msg {sent_message.id} "
            f"in #{channel.name} von {interaction.user}"
        )

    # ==================================================================
    # /reactionrole add <message_id> <emoji> <rolle>
    # ==================================================================

    @rr_grp.command(
        name="add",
        description="Emoji-Rollen-Paar zu einer Reaction-Role-Nachricht hinzufuegen",
    )
    @app_commands.describe(
        message_id="ID der Reaction-Role-Nachricht",
        emoji="Emoji das die Rolle ausloest",
        rolle="Rolle die zugewiesen wird",
    )
    @admin_only()
    async def rr_add(
        self,
        interaction: discord.Interaction,
        message_id: str,
        emoji: str,
        rolle: discord.Role,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        msg_id_str = message_id.strip()

        # Pruefen ob die Nachricht registriert ist
        if msg_id_str not in self._data:
            await interaction.followup.send(
                f"Nachricht `{msg_id_str}` ist nicht als Reaction-Role registriert.\n"
                f"Erstelle zuerst ein Embed mit `/reactionrole create`.",
                ephemeral=True,
            )
            return

        entry = self._data[msg_id_str]
        channel_id = entry.get("channel_id")

        # Emoji normalisieren (Custom Emojis vs. Unicode)
        emoji_str = emoji.strip()

        # Pruefen ob Emoji bereits registriert ist
        if emoji_str in entry.get("roles", {}):
            existing_role_id = entry["roles"][emoji_str]
            await interaction.followup.send(
                f"Emoji {emoji_str} ist bereits der Rolle <@&{existing_role_id}> zugewiesen.\n"
                f"Entferne zuerst mit `/reactionrole remove {msg_id_str} {emoji_str}`.",
                ephemeral=True,
            )
            return

        # Pruefen ob der Bot die Rolle zuweisen kann (Hierarchie)
        bot_member = interaction.guild.me
        if rolle >= bot_member.top_role:
            await interaction.followup.send(
                f"Die Rolle **{rolle.name}** ist hoeher als meine hoechste Rolle. "
                f"Ich kann sie nicht zuweisen.",
                ephemeral=True,
            )
            return

        # @everyone kann nicht zugewiesen werden
        if rolle.is_default():
            await interaction.followup.send(
                "Die @everyone-Rolle kann nicht als Reaction-Role verwendet werden.",
                ephemeral=True,
            )
            return

        # Nachricht holen und Reaktion hinzufuegen
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                await interaction.followup.send(
                    f"Channel {channel_id} nicht gefunden.", ephemeral=True
                )
                return

        try:
            message = await channel.fetch_message(int(msg_id_str))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                f"Nachricht `{msg_id_str}` nicht gefunden.", ephemeral=True
            )
            return

        # Bot-Reaktion hinzufuegen
        try:
            await message.add_reaction(emoji_str)
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"Konnte Emoji {emoji_str} nicht als Reaktion hinzufuegen: {e}",
                ephemeral=True,
            )
            return

        # Eintrag speichern
        entry.setdefault("roles", {})[emoji_str] = rolle.id
        await self._db_add_role(
            msg_id_str, entry["channel_id"], entry["guild_id"],
            emoji_str, rolle.id
        )

        # Embed der Nachricht aktualisieren mit Rollen-Info
        await self._update_message_embed(message, entry)

        await interaction.followup.send(
            f"Emoji-Rolle hinzugefuegt: {emoji_str} -> **{rolle.name}**\n"
            f"Nachricht: `{msg_id_str}`",
            ephemeral=True,
        )

        logger.info(
            f"Reaction-Role hinzugefuegt: {emoji_str} -> {rolle.name} "
            f"(Msg {msg_id_str}) von {interaction.user}"
        )

    # ==================================================================
    # /reactionrole remove <message_id> <emoji>
    # ==================================================================

    @rr_grp.command(
        name="remove",
        description="Emoji-Rollen-Paar von einer Reaction-Role-Nachricht entfernen",
    )
    @app_commands.describe(
        message_id="ID der Reaction-Role-Nachricht",
        emoji="Emoji das entfernt werden soll",
    )
    @admin_only()
    async def rr_remove(
        self,
        interaction: discord.Interaction,
        message_id: str,
        emoji: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        msg_id_str = message_id.strip()
        emoji_str = emoji.strip()

        # Pruefen ob Nachricht registriert ist
        if msg_id_str not in self._data:
            await interaction.followup.send(
                f"Nachricht `{msg_id_str}` ist nicht als Reaction-Role registriert.",
                ephemeral=True,
            )
            return

        entry = self._data[msg_id_str]
        roles_map = entry.get("roles", {})

        # Pruefen ob Emoji registriert ist
        if emoji_str not in roles_map:
            await interaction.followup.send(
                f"Emoji {emoji_str} ist nicht für Nachricht `{msg_id_str}` registriert.",
                ephemeral=True,
            )
            return

        removed_role_id = roles_map.pop(emoji_str)
        await self._db_remove_role(msg_id_str, emoji_str)

        # Bot-Reaktion von der Nachricht entfernen
        channel_id = entry.get("channel_id")
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None

        if channel:
            try:
                message = await channel.fetch_message(int(msg_id_str))
                await message.clear_reaction(emoji_str)
                # Embed aktualisieren
                await self._update_message_embed(message, entry)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.warning(
                    f"Konnte Reaktion {emoji_str} nicht entfernen: {e}"
                )

        # Eintrag komplett entfernen wenn keine Rollen mehr vorhanden
        if not roles_map:
            del self._data[msg_id_str]
            await self._db_remove_message(msg_id_str)

        await interaction.followup.send(
            f"Emoji-Rolle entfernt: {emoji_str} (war Rolle <@&{removed_role_id}>)\n"
            f"Nachricht: `{msg_id_str}`",
            ephemeral=True,
        )

        logger.info(
            f"Reaction-Role entfernt: {emoji_str} von Msg {msg_id_str} "
            f"von {interaction.user}"
        )

    # ==================================================================
    # /reactionrole list
    # ==================================================================

    @rr_grp.command(
        name="list",
        description="Alle registrierten Reaction-Role-Nachrichten anzeigen",
    )
    @admin_only()
    async def rr_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if not self._data:
            await interaction.followup.send(
                "Keine Reaction-Roles registriert.\n"
                "Erstelle eine mit `/reactionrole create`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Reaction Roles ({len(self._data)} Nachricht(en))",
            color=0x5865F2,
        )

        for msg_id_str, entry in self._data.items():
            channel_id = entry.get("channel_id")
            roles_map = entry.get("roles", {})

            # Channel-Mention erstellen
            channel_mention = f"<#{channel_id}>" if channel_id else "Unbekannt"

            # Rollen-Liste formatieren
            if roles_map:
                role_lines: list[str] = []
                for emoji_str, role_id in roles_map.items():
                    role_lines.append(f"{emoji_str} -> <@&{role_id}>")
                roles_text = "\n".join(role_lines)
            else:
                roles_text = "Keine Emoji-Rollen konfiguriert"

            embed.add_field(
                name=f"Nachricht: `{msg_id_str}`",
                value=(
                    f"**Channel:** {channel_mention}\n"
                    f"{roles_text}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # Listener: Reaktion hinzugefuegt
    # ==================================================================

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        """Rolle zuweisen wenn User auf Reaction-Role-Nachricht reagiert"""
        # Bots ignorieren
        if payload.user_id == self.bot.user.id:
            return

        msg_id_str = str(payload.message_id)
        if msg_id_str not in self._data:
            return

        entry = self._data[msg_id_str]
        roles_map = entry.get("roles", {})
        emoji_str = str(payload.emoji)

        if emoji_str not in roles_map:
            return

        role_id = roles_map[emoji_str]
        guild_id = entry.get("guild_id")

        # Guild holen
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            logger.warning(
                f"Reaction-Role: Guild {guild_id} nicht gefunden"
            )
            return

        # Rolle holen
        role = guild.get_role(role_id)
        if role is None:
            logger.warning(
                f"Reaction-Role: Rolle {role_id} nicht gefunden in Guild {guild_id}"
            )
            return

        # Member holen
        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except (discord.NotFound, discord.HTTPException):
                logger.warning(
                    f"Reaction-Role: Member {payload.user_id} nicht gefunden"
                )
                return

        # Bots ignorieren (doppelte Sicherheit)
        if member.bot:
            return

        # Rolle zuweisen
        try:
            await member.add_roles(role, reason="Reaction Role")
            logger.info(
                f"Reaction-Role: {member} hat Rolle '{role.name}' erhalten "
                f"(Emoji: {emoji_str}, Msg: {msg_id_str})"
            )
        except discord.Forbidden:
            logger.warning(
                f"Reaction-Role: Keine Berechtigung, Rolle '{role.name}' "
                f"an {member} zuzuweisen"
            )
        except discord.HTTPException as e:
            logger.error(
                f"Reaction-Role: Fehler beim Zuweisen von '{role.name}' "
                f"an {member}: {e}"
            )

    # ==================================================================
    # Listener: Reaktion entfernt
    # ==================================================================

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        """Rolle entfernen wenn User Reaktion von Reaction-Role-Nachricht entfernt"""
        # Bots ignorieren
        if payload.user_id == self.bot.user.id:
            return

        msg_id_str = str(payload.message_id)
        if msg_id_str not in self._data:
            return

        entry = self._data[msg_id_str]
        roles_map = entry.get("roles", {})
        emoji_str = str(payload.emoji)

        if emoji_str not in roles_map:
            return

        role_id = roles_map[emoji_str]
        guild_id = entry.get("guild_id")

        # Guild holen
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            logger.warning(
                f"Reaction-Role: Guild {guild_id} nicht gefunden"
            )
            return

        # Rolle holen
        role = guild.get_role(role_id)
        if role is None:
            logger.warning(
                f"Reaction-Role: Rolle {role_id} nicht gefunden in Guild {guild_id}"
            )
            return

        # Member holen
        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except (discord.NotFound, discord.HTTPException):
                logger.warning(
                    f"Reaction-Role: Member {payload.user_id} nicht gefunden"
                )
                return

        # Bots ignorieren (doppelte Sicherheit)
        if member.bot:
            return

        # Rolle entfernen
        try:
            await member.remove_roles(role, reason="Reaction Role entfernt")
            logger.info(
                f"Reaction-Role: {member} hat Rolle '{role.name}' verloren "
                f"(Emoji: {emoji_str}, Msg: {msg_id_str})"
            )
        except discord.Forbidden:
            logger.warning(
                f"Reaction-Role: Keine Berechtigung, Rolle '{role.name}' "
                f"von {member} zu entfernen"
            )
        except discord.HTTPException as e:
            logger.error(
                f"Reaction-Role: Fehler beim Entfernen von '{role.name}' "
                f"von {member}: {e}"
            )

    # ==================================================================
    # Hilfsmethoden
    # ==================================================================

    async def _update_message_embed(
        self,
        message: discord.Message,
        entry: dict[str, Any],
    ) -> None:
        """
        Embed einer Reaction-Role-Nachricht aktualisieren mit aktuellen Rollen.

        Fuegt die Rollen-Zuordnung als Feld zum bestehenden Embed hinzu.

        Race-Lock: per-message_id verhindert Lost-Updates bei concurrent Reactions
        (Read-Modify-Write der Embed-Felder).

        Args:
            message: Die Discord-Nachricht mit dem Embed
            entry: Der Reaction-Role-Eintrag aus self._data
        """
        async with self._get_panel_lock(message.id):
            roles_map = entry.get("roles", {})

            if not message.embeds:
                return

            embed = message.embeds[0].copy()

            # Bestehendes Rollen-Feld entfernen (falls vorhanden)
            new_fields: list[dict[str, Any]] = []
            for field in embed.fields:
                if field.name != "Rollen":
                    new_fields.append(
                        {"name": field.name, "value": field.value, "inline": field.inline}
                    )

            embed.clear_fields()
            for field in new_fields:
                embed.add_field(**field)

            # Rollen-Feld hinzufuegen (wenn Rollen vorhanden)
            if roles_map:
                role_lines: list[str] = []
                for emoji_str, role_id in roles_map.items():
                    role_lines.append(f"{emoji_str} — <@&{role_id}>")

                embed.add_field(
                    name="Rollen",
                    value="\n".join(role_lines),
                    inline=False,
                )

            try:
                await message.edit(embed=embed)
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Konnte Reaction-Role-Embed nicht aktualisieren: {e}")

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung für alle Commands in dieser Cog"""
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
    await bot.add_cog(ReactionRolesCog(bot))
