"""
Audit Logger — Phase 11g
Zentrales Audit-Logging für den Admin Bot.

Loggt Server-Events (Joins, Leaves, Rollen-Änderungen, Nick-Änderungen,
Channel-Änderungen, Moderations-Aktionen) als farbige Embeds in
konfigurierbare Discord-Kanäle.

Zusätzlich werden alle Events persistent in der SQLite-Datenbank
(Tabelle: audit_log) gespeichert.

Konfiguration in data/admin/audit_config.json:
{
  "mod_log_channel": 0,
  "join_log_channel": 0,
  "member_log_channel": 0,
  "role_log_channel": 0,
  "server_log_channel": 0
}

Die Discord-Kanal-Konfiguration wird in audit_config.json gespeichert.
Die Logs gehen an Discord-Kanäle UND in die SQLite-Datenbank.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import discord

from modules.database.db_manager import get_db
from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR

logger = get_logger("modules.audit_logger")

# Pfad zur Konfigurationsdatei
CONFIG_FILE = ADMIN_DATA_DIR / "audit_config.json"

# Standard-Konfiguration (alle Kanäle deaktiviert)
_DEFAULT_CONFIG = {
    "mod_log_channel": 0,
    "join_log_channel": 0,
    "member_log_channel": 0,
    "role_log_channel": 0,
    "server_log_channel": 0,
}

# Kategorie-Zuordnung für den /audit setup Command
CATEGORY_TO_KEY = {
    "mod": "mod_log_channel",
    "join": "join_log_channel",
    "member": "member_log_channel",
    "role": "role_log_channel",
    "server": "server_log_channel",
}

# Farben für verschiedene Log-Typen
COLOR_JOIN = 0x2ecc71       # Grün
COLOR_LEAVE = 0xe74c3c      # Rot
COLOR_MOD = 0xe67e22        # Orange
COLOR_NICK = 0x3498db       # Blau
COLOR_AVATAR = 0x9b59b6     # Lila
COLOR_ROLE_ADD = 0x2ecc71   # Grün
COLOR_ROLE_REMOVE = 0xe74c3c  # Rot
COLOR_CHANNEL_CREATE = 0x2ecc71  # Grün
COLOR_CHANNEL_DELETE = 0xe74c3c  # Rot
COLOR_SERVER = 0xf1c40f      # Gelb


class AuditLogger:
    """
    Audit-Logging-System für den Admin Bot.

    Loggt Server-Events als farbige Embeds in konfigurierbare
    Discord-Kanäle. Jede Log-Kategorie (Moderation, Joins, Member,
    Rollen, Server) hat einen eigenen konfigurierbaren Kanal.

    Zusätzlich werden alle Events persistent in der SQLite-Datenbank
    (Tabelle: audit_log) gespeichert.

    Die Konfiguration (Channel-IDs) wird in audit_config.json gespeichert.
    """

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._load_config()

    # ------------------------------------------------------------------
    # Konfiguration laden/speichern
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        """Audit-Konfiguration von Disk laden"""
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
                # Fehlende Keys mit Defaults ergaenzen
                for key, default_value in _DEFAULT_CONFIG.items():
                    self._config.setdefault(key, default_value)
                logger.info("Audit-Konfiguration geladen")
            else:
                self._config = _DEFAULT_CONFIG.copy()
                logger.info("Keine Audit-Konfiguration vorhanden, verwende Defaults")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Audit-Konfiguration laden fehlgeschlagen: {e}")
            self._config = _DEFAULT_CONFIG.copy()

    def _save_config(self) -> None:
        """Audit-Konfiguration auf Disk speichern"""
        try:
            ADMIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            logger.info("Audit-Konfiguration gespeichert")
        except IOError as e:
            logger.error(f"Audit-Konfiguration speichern fehlgeschlagen: {e}")

    def get_config(self) -> dict[str, Any]:
        """Aktuelle Konfiguration zurueckgeben (Kopie)"""
        return self._config.copy()

    def set_channel(self, category: str, channel_id: int) -> bool:
        """
        Kanal fuer eine Log-Kategorie setzen.

        Args:
            category: Kategorie-Name (mod, join, member, role, server)
            channel_id: Discord-Channel-ID

        Returns:
            True wenn erfolgreich, False wenn ungueltige Kategorie
        """
        config_key = CATEGORY_TO_KEY.get(category)
        if not config_key:
            return False

        self._config[config_key] = channel_id
        self._save_config()
        logger.info(f"Audit-Kanal gesetzt: {category} -> {channel_id}")
        return True

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    async def _get_channel(
        self, bot: discord.Client, config_key: str
    ) -> Optional[discord.TextChannel]:
        """
        Discord-Kanal anhand des Config-Keys ermitteln.

        Args:
            bot: Bot-Instanz
            config_key: Schluessel in der Konfiguration (z.B. "mod_log_channel")

        Returns:
            TextChannel oder None wenn nicht konfiguriert/gefunden
        """
        channel_id = self._config.get(config_key, 0)
        if not channel_id:
            return None

        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning(f"Audit-Kanal {channel_id} nicht erreichbar ({config_key})")
                return None

        if not isinstance(channel, discord.TextChannel):
            logger.warning(f"Audit-Kanal {channel_id} ist kein Textkanal ({config_key})")
            return None

        return channel

    async def _send_embed(
        self, bot: discord.Client, config_key: str, embed: discord.Embed
    ) -> bool:
        """
        Embed an den konfigurierten Kanal senden.

        Args:
            bot: Bot-Instanz
            config_key: Konfigurationsschlüssel für den Kanal
            embed: Das zu sendende Embed

        Returns:
            True wenn erfolgreich gesendet
        """
        channel = await self._get_channel(bot, config_key)
        if not channel:
            return False

        try:
            await channel.send(embed=embed)
            return True
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Audit-Embed senden fehlgeschlagen ({config_key}): {e}")
            return False

    async def _log_to_db(
        self,
        action: str,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        target: Optional[str] = None,
        details: Optional[str] = None,
        source: str = "bot",
    ) -> None:
        """
        Audit-Event persistent in die SQLite-Datenbank schreiben.

        Args:
            action: Art der Aktion (z.B. "mod_ban", "member_join", "role_add")
            user_id: Discord-User-ID des Ausführenden (als String)
            user_name: Anzeigename des Ausführenden
            target: Ziel der Aktion (z.B. User-ID, Channel-Name, Rollen-Name)
            details: Zusätzliche Details (JSON-String oder Freitext)
            source: Quelle des Events (Standard: "bot")
        """
        try:
            db = await get_db()
            await db.execute(
                "INSERT INTO audit_log (user_id, user_name, action, target, details, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, user_name, action, target, details, source),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Audit-Log DB-Schreiben fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Log-Methoden
    # ------------------------------------------------------------------

    async def log_mod_action(
        self,
        bot: discord.Client,
        action: str,
        target: discord.Member | discord.User,
        moderator: discord.Member | discord.User,
        reason: Optional[str] = None,
    ) -> bool:
        """
        Moderations-Aktion loggen (Ban, Kick, Mute, Warn, etc.)

        Args:
            bot: Bot-Instanz
            action: Art der Aktion (z.B. "Ban", "Kick", "Warn", "Mute")
            target: Betroffener User
            moderator: Ausführender Moderator
            reason: Grund für die Aktion

        Returns:
            True wenn erfolgreich geloggt
        """
        embed = discord.Embed(
            title=f"Moderation: {action}",
            color=COLOR_MOD,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="Betroffener",
            value=f"{target.mention} (`{target}`, ID: {target.id})",
            inline=False,
        )
        embed.add_field(
            name="Moderator",
            value=f"{moderator.mention} (`{moderator}`)",
            inline=False,
        )
        embed.add_field(
            name="Grund",
            value=reason or "Kein Grund angegeben",
            inline=False,
        )

        if hasattr(target, "display_avatar") and target.display_avatar:
            embed.set_thumbnail(url=target.display_avatar.url)

        embed.set_footer(text=f"User-ID: {target.id}")

        # SQLite-Logging
        await self._log_to_db(
            action=f"mod_{action.lower()}",
            user_id=str(moderator.id),
            user_name=str(moderator),
            target=f"{target} ({target.id})",
            details=reason,
            source="mod",
        )

        return await self._send_embed(bot, "mod_log_channel", embed)

    async def log_join(
        self, bot: discord.Client, member: discord.Member
    ) -> bool:
        """
        Member-Join loggen mit Account-Alter.

        Args:
            bot: Bot-Instanz
            member: Beigetretenes Mitglied

        Returns:
            True wenn erfolgreich geloggt
        """
        now = datetime.now(timezone.utc)
        created_at = member.created_at

        # Account-Alter berechnen
        account_age = now - created_at
        days = account_age.days
        if days >= 365:
            years = days // 365
            remaining_days = days % 365
            age_str = f"{years} Jahr(e), {remaining_days} Tag(e)"
        elif days >= 30:
            months = days // 30
            remaining_days = days % 30
            age_str = f"{months} Monat(e), {remaining_days} Tag(e)"
        else:
            age_str = f"{days} Tag(e)"

        # Warnung bei jungem Account (< 7 Tage)
        warning = ""
        if days < 7:
            warning = "\n**Achtung: Neuer Account!**"

        embed = discord.Embed(
            title="Mitglied beigetreten",
            description=(
                f"{member.mention} (`{member}`) ist dem Server beigetreten."
                f"{warning}"
            ),
            color=COLOR_JOIN,
            timestamp=now,
        )

        embed.add_field(
            name="Account erstellt",
            value=f"{discord.utils.format_dt(created_at, 'R')} ({age_str})",
            inline=False,
        )

        # Mitglieder-Anzahl
        if member.guild:
            embed.add_field(
                name="Mitglieder gesamt",
                value=str(member.guild.member_count),
                inline=True,
            )

        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)

        embed.set_footer(text=f"User-ID: {member.id}")

        # SQLite-Logging
        await self._log_to_db(
            action="member_join",
            user_id=str(member.id),
            user_name=str(member),
            target=member.guild.name if member.guild else None,
            details=f"Account-Alter: {age_str}",
        )

        return await self._send_embed(bot, "join_log_channel", embed)

    async def log_leave(
        self, bot: discord.Client, member: discord.Member
    ) -> bool:
        """
        Member-Leave loggen.

        Args:
            bot: Bot-Instanz
            member: Ausgetretenes Mitglied

        Returns:
            True wenn erfolgreich geloggt
        """
        now = datetime.now(timezone.utc)

        # Rollen des Members (ohne @everyone)
        roles = [r.mention for r in member.roles if r.name != "@everyone"]
        roles_str = ", ".join(roles) if roles else "Keine"

        # Mitgliedschaftsdauer berechnen
        joined_at = member.joined_at
        if joined_at:
            membership = now - joined_at
            days = membership.days
            if days >= 365:
                years = days // 365
                remaining_days = days % 365
                membership_str = f"{years} Jahr(e), {remaining_days} Tag(e)"
            elif days >= 30:
                months = days // 30
                remaining_days = days % 30
                membership_str = f"{months} Monat(e), {remaining_days} Tag(e)"
            else:
                membership_str = f"{days} Tag(e)"
        else:
            membership_str = "Unbekannt"

        embed = discord.Embed(
            title="Mitglied verlassen",
            description=f"`{member}` hat den Server verlassen.",
            color=COLOR_LEAVE,
            timestamp=now,
        )

        embed.add_field(
            name="Mitgliedschaftsdauer",
            value=membership_str,
            inline=True,
        )
        embed.add_field(
            name="Rollen",
            value=roles_str[:1024],  # Embed-Feld-Limit beachten
            inline=False,
        )

        if member.guild:
            embed.add_field(
                name="Mitglieder gesamt",
                value=str(member.guild.member_count),
                inline=True,
            )

        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)

        embed.set_footer(text=f"User-ID: {member.id}")

        # SQLite-Logging
        role_names = [r.name for r in member.roles if r.name != "@everyone"]
        await self._log_to_db(
            action="member_leave",
            user_id=str(member.id),
            user_name=str(member),
            target=member.guild.name if member.guild else None,
            details=f"Mitgliedschaft: {membership_str}, Rollen: {', '.join(role_names) if role_names else 'Keine'}",
        )

        return await self._send_embed(bot, "join_log_channel", embed)

    async def log_member_update(
        self,
        bot: discord.Client,
        before: discord.Member,
        after: discord.Member,
    ) -> bool:
        """
        Nickname- oder Avatar-Änderungen loggen.

        Args:
            bot: Bot-Instanz
            before: Member-Zustand vorher
            after: Member-Zustand nachher

        Returns:
            True wenn eine Änderung geloggt wurde
        """
        logged = False

        # Nickname-Änderung
        if before.nick != after.nick:
            embed = discord.Embed(
                title="Nickname geändert",
                description=f"{after.mention} (`{after}`)",
                color=COLOR_NICK,
                timestamp=datetime.now(timezone.utc),
            )

            old_nick = before.nick or before.name
            new_nick = after.nick or after.name

            embed.add_field(name="Vorher", value=old_nick, inline=True)
            embed.add_field(name="Nachher", value=new_nick, inline=True)

            if after.display_avatar:
                embed.set_thumbnail(url=after.display_avatar.url)

            embed.set_footer(text=f"User-ID: {after.id}")

            # SQLite-Logging
            await self._log_to_db(
                action="nick_change",
                user_id=str(after.id),
                user_name=str(after),
                target=f"{old_nick} -> {new_nick}",
                details=f"Vorher: {old_nick}, Nachher: {new_nick}",
            )

            sent = await self._send_embed(bot, "member_log_channel", embed)
            if sent:
                logged = True

        # Avatar-Änderung (Guild-spezifisch)
        if before.guild_avatar != after.guild_avatar:
            embed = discord.Embed(
                title="Server-Avatar geändert",
                description=f"{after.mention} (`{after}`)",
                color=COLOR_AVATAR,
                timestamp=datetime.now(timezone.utc),
            )

            if before.guild_avatar:
                embed.add_field(
                    name="Vorheriger Avatar",
                    value=f"[Link]({before.guild_avatar.url})",
                    inline=True,
                )
            else:
                embed.add_field(name="Vorheriger Avatar", value="Keiner", inline=True)

            if after.guild_avatar:
                embed.set_thumbnail(url=after.guild_avatar.url)
                embed.add_field(
                    name="Neuer Avatar",
                    value=f"[Link]({after.guild_avatar.url})",
                    inline=True,
                )
            else:
                embed.add_field(name="Neuer Avatar", value="Entfernt", inline=True)

            embed.set_footer(text=f"User-ID: {after.id}")

            # SQLite-Logging
            await self._log_to_db(
                action="avatar_change",
                user_id=str(after.id),
                user_name=str(after),
                details="Server-Avatar geändert",
            )

            sent = await self._send_embed(bot, "member_log_channel", embed)
            if sent:
                logged = True

        return logged

    async def log_role_change(
        self,
        bot: discord.Client,
        member: discord.Member,
        before_roles: list[discord.Role],
        after_roles: list[discord.Role],
    ) -> bool:
        """
        Rollen-Änderungen loggen (hinzugefügt/entfernt).

        Args:
            bot: Bot-Instanz
            member: Betroffenes Mitglied
            before_roles: Rollen vorher
            after_roles: Rollen nachher

        Returns:
            True wenn eine Änderung geloggt wurde
        """
        before_set = set(r.id for r in before_roles)
        after_set = set(r.id for r in after_roles)

        added_ids = after_set - before_set
        removed_ids = before_set - after_set

        if not added_ids and not removed_ids:
            return False

        logged = False

        # Rollen hinzugefügt
        if added_ids:
            added_roles = [r for r in after_roles if r.id in added_ids]
            roles_str = ", ".join(r.mention for r in added_roles)

            embed = discord.Embed(
                title="Rolle(n) hinzugefügt",
                description=f"{member.mention} (`{member}`)",
                color=COLOR_ROLE_ADD,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(
                name="Hinzugefügt",
                value=roles_str[:1024],
                inline=False,
            )

            if member.display_avatar:
                embed.set_thumbnail(url=member.display_avatar.url)

            embed.set_footer(text=f"User-ID: {member.id}")

            # SQLite-Logging
            role_names = [r.name for r in added_roles]
            await self._log_to_db(
                action="role_add",
                user_id=str(member.id),
                user_name=str(member),
                target=", ".join(role_names),
                details=f"Rollen hinzugefügt: {', '.join(role_names)}",
            )

            sent = await self._send_embed(bot, "role_log_channel", embed)
            if sent:
                logged = True

        # Rollen entfernt
        if removed_ids:
            removed_roles = [r for r in before_roles if r.id in removed_ids]
            roles_str = ", ".join(r.mention for r in removed_roles)

            embed = discord.Embed(
                title="Rolle(n) entfernt",
                description=f"{member.mention} (`{member}`)",
                color=COLOR_ROLE_REMOVE,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(
                name="Entfernt",
                value=roles_str[:1024],
                inline=False,
            )

            if member.display_avatar:
                embed.set_thumbnail(url=member.display_avatar.url)

            embed.set_footer(text=f"User-ID: {member.id}")

            # SQLite-Logging
            role_names = [r.name for r in removed_roles]
            await self._log_to_db(
                action="role_remove",
                user_id=str(member.id),
                user_name=str(member),
                target=", ".join(role_names),
                details=f"Rollen entfernt: {', '.join(role_names)}",
            )

            sent = await self._send_embed(bot, "role_log_channel", embed)
            if sent:
                logged = True

        return logged

    async def log_channel_change(
        self,
        bot: discord.Client,
        action: str,
        channel: discord.abc.GuildChannel,
    ) -> bool:
        """
        Channel-Änderungen loggen (erstellt/gelöscht).

        Args:
            bot: Bot-Instanz
            action: "erstellt" oder "gelöscht"
            channel: Betroffener Kanal

        Returns:
            True wenn erfolgreich geloggt
        """
        is_create = action.lower() in ("erstellt", "create", "created")
        color = COLOR_CHANNEL_CREATE if is_create else COLOR_CHANNEL_DELETE

        # Channel-Typ bestimmen
        type_map = {
            discord.ChannelType.text: "Text",
            discord.ChannelType.voice: "Sprache",
            discord.ChannelType.category: "Kategorie",
            discord.ChannelType.stage_voice: "Stage",
            discord.ChannelType.forum: "Forum",
            discord.ChannelType.news: "Ankündigungen",
        }
        channel_type = type_map.get(channel.type, str(channel.type))

        embed = discord.Embed(
            title=f"Kanal {action}",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(name="Name", value=channel.name, inline=True)
        embed.add_field(name="Typ", value=channel_type, inline=True)
        embed.add_field(name="ID", value=str(channel.id), inline=True)

        # Kategorie anzeigen (falls vorhanden)
        if hasattr(channel, "category") and channel.category:
            embed.add_field(
                name="Kategorie",
                value=channel.category.name,
                inline=True,
            )

        embed.set_footer(text=f"Channel-ID: {channel.id}")

        # SQLite-Logging
        db_action = "channel_create" if is_create else "channel_delete"
        category_name = channel.category.name if hasattr(channel, "category") and channel.category else None
        await self._log_to_db(
            action=db_action,
            target=f"{channel.name} ({channel.id})",
            details=f"Typ: {channel_type}, Kategorie: {category_name or 'Keine'}",
        )

        return await self._send_embed(bot, "server_log_channel", embed)

    async def log_role_create(
        self, bot: discord.Client, role: discord.Role
    ) -> bool:
        """
        Server-Rolle erstellt loggen.

        Args:
            bot: Bot-Instanz
            role: Erstellte Rolle

        Returns:
            True wenn erfolgreich geloggt
        """
        embed = discord.Embed(
            title="Rolle erstellt",
            color=COLOR_CHANNEL_CREATE,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(name="Name", value=role.name, inline=True)
        embed.add_field(name="Farbe", value=str(role.color), inline=True)
        embed.add_field(name="ID", value=str(role.id), inline=True)
        embed.add_field(
            name="Mentionbar",
            value="Ja" if role.mentionable else "Nein",
            inline=True,
        )
        embed.add_field(
            name="Hoisted",
            value="Ja" if role.hoist else "Nein",
            inline=True,
        )

        embed.set_footer(text=f"Rollen-ID: {role.id}")

        # SQLite-Logging
        await self._log_to_db(
            action="role_create",
            target=f"{role.name} ({role.id})",
            details=f"Farbe: {role.color}, Mentionbar: {role.mentionable}, Hoisted: {role.hoist}",
        )

        return await self._send_embed(bot, "server_log_channel", embed)

    async def log_role_delete(
        self, bot: discord.Client, role: discord.Role
    ) -> bool:
        """
        Server-Rolle geloescht loggen.

        Args:
            bot: Bot-Instanz
            role: Geloeschte Rolle

        Returns:
            True wenn erfolgreich geloggt
        """
        embed = discord.Embed(
            title="Rolle geloescht",
            color=COLOR_CHANNEL_DELETE,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(name="Name", value=role.name, inline=True)
        embed.add_field(name="Farbe", value=str(role.color), inline=True)
        embed.add_field(name="ID", value=str(role.id), inline=True)

        embed.set_footer(text=f"Rollen-ID: {role.id}")

        # SQLite-Logging
        await self._log_to_db(
            action="role_delete",
            target=f"{role.name} ({role.id})",
            details=f"Farbe: {role.color}",
        )

        return await self._send_embed(bot, "server_log_channel", embed)
