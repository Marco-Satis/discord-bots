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
  "server_log_channel": 0,
  "message_log_channel": 0,
  "voice_log_channel": 0
}

Erweiterung 20.10.2026 (Marco, nach Maki-Vorbild): Nachrichten geloescht/bearbeitet,
Massenloeschung, Voice beitreten/wechseln/verlassen, Bann/Unbann/Kick/Timeout mit
Moderator aus dem Discord-Audit-Log, Invites angelegt/geloescht. Der Inhalt
geloeschter Nachrichten steht nur im Log-Embed, nicht in der Datenbank.

Die Discord-Kanal-Konfiguration wird in audit_config.json gespeichert.
Die Logs gehen an Discord-Kanäle UND in die SQLite-Datenbank.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import discord

from modules.database.db_manager import get_db
from modules.guild_context import GuildConfig
from utils.embeds import (
    base_embed,
    error_embed,
    info_embed,
    success_embed,
    warning_embed,
    COLOR_BRAND,
    COLOR_ERROR,
    COLOR_INFO,
    COLOR_SUCCESS,
    COLOR_WARNING,
)
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
    "message_log_channel": 0,
    "voice_log_channel": 0,
}

# Kategorie-Zuordnung für den /audit setup Command
CATEGORY_TO_KEY = {
    "mod": "mod_log_channel",
    "join": "join_log_channel",
    "member": "member_log_channel",
    "role": "role_log_channel",
    "server": "server_log_channel",
    "message": "message_log_channel",
    "voice": "voice_log_channel",
}

# Konten juenger als das gelten im Join-Log als „neu“
NEW_ACCOUNT_DAYS = 7
# Maximale Laenge zitierter Nachrichteninhalte im Log-Embed
CONTENT_LIMIT = 1000



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
        self._mtime: float = 0.0
        self._load_config()

    def _neu_laden_wenn_geaendert(self) -> None:
        """
        Datei neu lesen, wenn ein anderer Prozess sie geaendert hat.

        Seit 21.10.2026 stellt auch das Dashboard die Log-Kanaele ein (Seite /logs).
        Ohne diesen Abgleich galt eine Aenderung erst nach dem naechsten Marshal-Neustart.
        """
        try:
            mtime = CONFIG_FILE.stat().st_mtime
        except OSError:
            return
        if mtime != self._mtime:
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
                self._mtime = CONFIG_FILE.stat().st_mtime
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
            # Atomar (tmp + replace): Marshal und Dashboard schreiben dieselbe Datei
            tmp = CONFIG_FILE.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            os.replace(tmp, CONFIG_FILE)
            self._mtime = CONFIG_FILE.stat().st_mtime
            logger.info("Audit-Konfiguration gespeichert")
        except IOError as e:
            logger.error(f"Audit-Konfiguration speichern fehlgeschlagen: {e}")

    def get_config(self) -> dict[str, Any]:
        """Aktuelle Konfiguration zurueckgeben (Kopie)"""
        self._neu_laden_wenn_geaendert()
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
        self._neu_laden_wenn_geaendert()
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
        # Schalter aus der Modulübersicht im Dashboard (guild_modules, 15 s Cache).
        # Gesperrt wird nur das Posten; der Eintrag in audit_log entsteht weiter.
        if not await GuildConfig.is_module_enabled(channel.guild.id, "audit"):
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
        moderator: Optional[discord.Member | discord.User] = None,
        reason: Optional[str] = None,
        until: Optional[datetime] = None,
    ) -> bool:
        """
        Moderations-Aktion loggen (Ban, Kick, Mute, Warn, etc.)

        Args:
            bot: Bot-Instanz
            action: Art der Aktion (z.B. "Ban", "Kick", "Warn", "Mute")
            target: Betroffener User
            moderator: Ausführender Moderator (None = aus dem Discord-Audit-Log
                nicht ermittelbar)
            reason: Grund für die Aktion
            until: Ende einer befristeten Strafe (Timeout)

        Returns:
            True wenn erfolgreich geloggt
        """
        embed = warning_embed(
            title=f"Moderation: {action}",
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="Betroffener",
            value=f"{target.mention} (`{target}`, ID: {target.id})",
            inline=False,
        )
        embed.add_field(
            name="Moderator",
            value=f"{moderator.mention} (`{moderator}`)" if moderator else "unbekannt",
            inline=False,
        )
        embed.add_field(
            name="Grund",
            value=(reason or "Kein Grund angegeben")[:1024],
            inline=False,
        )
        if until:
            embed.add_field(
                name="Bis",
                value=discord.utils.format_dt(until, "f"),
                inline=False,
            )

        if hasattr(target, "display_avatar") and target.display_avatar:
            embed.set_thumbnail(url=target.display_avatar.url)

        embed.set_footer(text=f"User-ID: {target.id}")

        # SQLite-Logging
        await self._log_to_db(
            action=f"mod_{action.lower()}",
            user_id=str(moderator.id) if moderator else None,
            user_name=str(moderator) if moderator else None,
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

        # Markierung neuer Konten (nur hier im Log-Kanal, nie oeffentlich):
        # junge Konten und Konten ohne eigenen Avatar sind typische Raid-Muster.
        neu = days < NEW_ACCOUNT_DAYS
        ohne_avatar = member.avatar is None
        warning = ""
        if neu:
            warning = f"\n⚠️ **Neues Konto** (jünger als {NEW_ACCOUNT_DAYS} Tage)"
        if ohne_avatar:
            warning += "\nKein eigenes Profilbild"

        _join_fn = warning_embed if neu else success_embed
        embed = _join_fn(
            title="⚠️ Neues Konto beigetreten" if neu else "Mitglied beigetreten",
            description=(
                f"{member.mention} (`{member}`) ist dem Server beigetreten."
                f"{warning}"
            ),
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
            details=f"Account-Alter: {age_str}" + (" (neu)" if neu else ""),
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

        embed = error_embed(
            title="Mitglied verlassen",
            description=f"`{member}` hat den Server verlassen.",
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
            embed = info_embed(
                title="Nickname geändert",
                description=f"{after.mention} (`{after}`)",
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
            embed = base_embed(
                title="Server-Avatar geändert",
                description=f"{after.mention} (`{after}`)",
                color=COLOR_BRAND,
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

            embed = success_embed(
                title="Rolle(n) hinzugefügt",
                description=f"{member.mention} (`{member}`)",
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

            embed = error_embed(
                title="Rolle(n) entfernt",
                description=f"{member.mention} (`{member}`)",
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

    async def log_server_ereignis(
        self,
        bot: discord.Client,
        titel: str,
        felder: list[tuple[str, str]],
        db_aktion: str,
        akteur: Optional[discord.abc.User] = None,
        farbe: int = COLOR_WARNING,
    ) -> bool:
        """
        Allgemeines Server-Ereignis (Webhooks, Threads) ins Server-Log + audit_log.

        Ergaenzt 21.10.2026: Webhooks und Threads tauchten bisher nirgends auf. Ein
        neu angelegter Webhook ist ein typisches Zeichen einer Kontouebernahme.
        """
        embed = base_embed(title=titel, color=farbe, timestamp=datetime.now(timezone.utc))
        for name, wert in felder:
            embed.add_field(name=name, value=str(wert)[:1024] or "–", inline=True)
        if akteur is not None:
            embed.add_field(name="Von", value=f"{akteur.mention} ({akteur})", inline=True)
        await self._log_to_db(
            db_aktion,
            user_id=str(akteur.id) if akteur else None,
            user_name=str(akteur) if akteur else None,
            target=next((w for n, w in felder if n in ("Kanal", "Thread")), None),
            details="; ".join(f"{n}: {w}" for n, w in felder),
        )
        return await self._send_embed(bot, "server_log_channel", embed)

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

        _kanal_fn = success_embed if is_create else error_embed
        embed = _kanal_fn(
            title=f"Kanal {action}",
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
        embed = success_embed(
            title="Rolle erstellt",
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
        embed = error_embed(
            title="Rolle geloescht",
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

    # ------------------------------------------------------------------
    # Erweiterung 20.10.2026: Nachrichten, Voice, Invites
    # ------------------------------------------------------------------

    def is_log_channel(self, channel_id: int) -> bool:
        """Ist der Kanal selbst ein Audit-Kanal? Dort wird nichts geloggt (Schleifen)."""
        return channel_id in {v for v in self._config.values() if isinstance(v, int) and v}

    @staticmethod
    def zitat(text: Optional[str]) -> str:
        """Nachrichtentext fuer ein Embed-Feld: Markdown entschaerft, auf CONTENT_LIMIT gekuerzt."""
        text = discord.utils.escape_markdown(text or "")
        if len(text) > CONTENT_LIMIT:
            text = text[:CONTENT_LIMIT] + " …"
        return text or "*(kein Text)*"

    async def log_message_delete(
        self, bot: discord.Client, message: discord.Message
    ) -> bool:
        """Geloeschte Nachricht (aus dem Cache) mit Inhalt loggen."""
        embed = error_embed(
            title="Nachricht gelöscht",
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Autor", value=f"{message.author.mention} (`{message.author}`)", inline=True)
        embed.add_field(name="Kanal", value=message.channel.mention, inline=True)
        embed.add_field(name="Inhalt", value=self.zitat(message.content), inline=False)
        if message.attachments:
            embed.add_field(
                name="Anhänge",
                value="\n".join(a.filename for a in message.attachments)[:1024],
                inline=False,
            )
        embed.set_footer(text=f"User-ID: {message.author.id} · Nachricht: {message.id}")

        # Inhalt bewusst NICHT in die DB (nur Laenge und Anzahl Anhaenge)
        await self._log_to_db(
            action="message_delete",
            user_id=str(message.author.id),
            user_name=str(message.author),
            target=f"#{getattr(message.channel, 'name', message.channel.id)}",
            details=f"{len(message.content or '')} Zeichen, {len(message.attachments)} Anhang/Anhänge",
        )
        return await self._send_embed(bot, "message_log_channel", embed)

    async def log_message_delete_uncached(
        self, bot: discord.Client, channel_id: int, message_id: int
    ) -> bool:
        """Geloeschte Nachricht, die nicht im Cache lag (aelter als der Bot-Start)."""
        embed = error_embed(
            title="Nachricht gelöscht",
            description=f"In <#{channel_id}>. Die Nachricht ist älter als der Bot-Start, Inhalt unbekannt.",
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Nachricht: {message_id}")
        return await self._send_embed(bot, "message_log_channel", embed)

    async def log_bulk_delete(
        self, bot: discord.Client, channel_id: int, count: int
    ) -> bool:
        """Massenloeschung (z. B. /clear) loggen."""
        embed = warning_embed(
            title="Massenlöschung",
            description=f"**{count}** Nachrichten in <#{channel_id}> gelöscht.",
            timestamp=datetime.now(timezone.utc),
        )
        await self._log_to_db(action="message_bulk_delete", target=str(channel_id), details=f"{count} Nachrichten")
        return await self._send_embed(bot, "message_log_channel", embed)

    async def log_message_edit(
        self, bot: discord.Client, before: discord.Message, after: discord.Message
    ) -> bool:
        """Bearbeitete Nachricht mit altem und neuem Text loggen."""
        embed = info_embed(
            title="Nachricht bearbeitet",
            description=f"[Zur Nachricht]({after.jump_url})",
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Autor", value=f"{after.author.mention} (`{after.author}`)", inline=True)
        embed.add_field(name="Kanal", value=after.channel.mention, inline=True)
        embed.add_field(name="Vorher", value=self.zitat(before.content), inline=False)
        embed.add_field(name="Nachher", value=self.zitat(after.content), inline=False)
        embed.set_footer(text=f"User-ID: {after.author.id} · Nachricht: {after.id}")
        await self._log_to_db(
            action="message_edit",
            user_id=str(after.author.id),
            user_name=str(after.author),
            target=f"#{getattr(after.channel, 'name', after.channel.id)}",
        )
        return await self._send_embed(bot, "message_log_channel", embed)

    async def log_voice(
        self,
        bot: discord.Client,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> bool:
        """Voice beitreten / wechseln / verlassen loggen (Mute/Deafen nicht)."""
        if before.channel == after.channel:
            return False
        if before.channel is None:
            titel, aktion = "Voice beigetreten", "voice_join"
            text = f"{member.mention} ist {after.channel.mention} beigetreten."
            embed = success_embed(title=titel, description=text, timestamp=datetime.now(timezone.utc))
        elif after.channel is None:
            titel, aktion = "Voice verlassen", "voice_leave"
            text = f"{member.mention} hat {before.channel.mention} verlassen."
            embed = error_embed(title=titel, description=text, timestamp=datetime.now(timezone.utc))
        else:
            titel, aktion = "Voice gewechselt", "voice_move"
            text = f"{member.mention}: {before.channel.mention} → {after.channel.mention}"
            embed = info_embed(title=titel, description=text, timestamp=datetime.now(timezone.utc))
        embed.set_footer(text=f"User-ID: {member.id}")
        await self._log_to_db(
            action=aktion,
            user_id=str(member.id),
            user_name=str(member),
            target=getattr(after.channel or before.channel, "name", None),
        )
        return await self._send_embed(bot, "voice_log_channel", embed)

    async def log_invite(
        self, bot: discord.Client, invite: discord.Invite, created: bool
    ) -> bool:
        """Einladung erstellt oder geloescht loggen."""
        _inv_fn = success_embed if created else error_embed
        embed = _inv_fn(
            title="Einladung erstellt" if created else "Einladung gelöscht",
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Code", value=f"`{invite.code}`", inline=True)
        if invite.channel is not None:
            embed.add_field(name="Kanal", value=f"<#{invite.channel.id}>", inline=True)
        if created:
            if invite.inviter:
                embed.add_field(name="Erstellt von", value=f"{invite.inviter.mention} (`{invite.inviter}`)", inline=False)
            embed.add_field(
                name="Gültig",
                value=(f"{invite.max_age // 3600} h" if invite.max_age else "unbegrenzt")
                + (f", max. {invite.max_uses} Nutzungen" if invite.max_uses else ""),
                inline=False,
            )
        await self._log_to_db(
            action="invite_create" if created else "invite_delete",
            user_id=str(invite.inviter.id) if created and invite.inviter else None,
            user_name=str(invite.inviter) if created and invite.inviter else None,
            target=invite.code,
        )
        return await self._send_embed(bot, "server_log_channel", embed)
