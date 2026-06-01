"""
Server Backup Manager — Phase 12e (F19)
Vollstaendige Discord-Server-Struktur-Snapshots.

Erstellt, verwaltet und restauriert Backups der gesamten Server-Struktur
(Kategorien, Channels, Rollen, Einstellungen, Emojis).

Persistenz: data/admin/server_backups/ — eine JSON-Datei pro Backup.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import discord

from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR

logger = get_logger("modules.server_backup")

# Verzeichnis fuer Backup-Dateien
BACKUP_DIR = ADMIN_DATA_DIR / "server_backups"


class ServerBackupManager:
    """
    Manager für Discord-Server-Struktur-Backups.

    Erstellt vollständige Snapshots der Server-Struktur und ermöglicht
    Vergleich und Wiederherstellung. Jedes Backup wird als eigene
    JSON-Datei in data/admin/server_backups/ gespeichert.
    """

    # Regex für gültige Backup-IDs (nur alphanumerisch + Bindestriche)
    _VALID_ID_RE = re.compile(r'^[a-zA-Z0-9\-]+$')

    def __init__(self) -> None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        self._restore_lock = asyncio.Lock()
        logger.info("ServerBackupManager initialisiert")

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _backup_path(self, backup_id: str) -> Path:
        """Dateipfad für ein Backup anhand der ID.

        Validiert die ID gegen Path-Traversal-Angriffe.

        Raises:
            ValueError: Wenn die Backup-ID ungültige Zeichen enthält.
        """
        if not self._VALID_ID_RE.match(backup_id):
            raise ValueError(f"Ungültige Backup-ID: {backup_id}")
        path = (BACKUP_DIR / f"{backup_id}.json").resolve()
        if not str(path).startswith(str(BACKUP_DIR.resolve())):
            raise ValueError(f"Path-Traversal erkannt: {backup_id}")
        return path

    def _save_backup(self, data: dict) -> None:
        """Backup-Daten auf Disk speichern"""
        path = self._backup_path(data["backup_id"])
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info(f"Backup gespeichert: {data['backup_id']}")
        except IOError as e:
            logger.error(f"Backup speichern fehlgeschlagen: {e}")
            raise

    def _load_backup(self, backup_id: str) -> Optional[dict]:
        """Einzelnes Backup von Disk laden"""
        path = self._backup_path(backup_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Backup laden fehlgeschlagen ({backup_id}): {e}")
            return None

    @staticmethod
    def _serialize_overwrites(
        overwrites: dict[Any, discord.PermissionOverwrite],
    ) -> list[dict]:
        """
        Permission-Overwrites in serialisierbares Format umwandeln.

        Speichert sowohl Rollen- als auch Member-Overwrites mit ID und Typ.
        """
        result: list[dict] = []
        for target, overwrite in overwrites.items():
            allow, deny = overwrite.pair()
            entry = {
                "id": target.id,
                "type": "role" if isinstance(target, discord.Role) else "member",
                "allow": allow.value,
                "deny": deny.value,
            }
            result.append(entry)
        return result

    @staticmethod
    def _channel_type_str(channel_type: discord.ChannelType) -> str:
        """Discord-ChannelType in lesbaren String umwandeln"""
        type_map = {
            discord.ChannelType.text: "text",
            discord.ChannelType.voice: "voice",
            discord.ChannelType.category: "category",
            discord.ChannelType.news: "news",
            discord.ChannelType.stage_voice: "stage",
            discord.ChannelType.forum: "forum",
        }
        return type_map.get(channel_type, str(channel_type))

    # ------------------------------------------------------------------
    # Backup erstellen
    # ------------------------------------------------------------------

    async def create_backup(
        self, guild: discord.Guild, created_by_id: int
    ) -> dict:
        """
        Vollstaendigen Snapshot der Server-Struktur erstellen.

        Erfasst: Kategorien, Channels, Rollen, Server-Einstellungen, Emojis
        und Welcome-Screen-Einstellungen.

        Args:
            guild: Der Discord-Server
            created_by_id: User-ID des Erstellers

        Returns:
            Vollstaendiges Backup-Dict mit allen Server-Daten
        """
        backup_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc)

        logger.info(
            f"Erstelle Server-Backup fuer '{guild.name}' "
            f"(ID: {guild.id}, von User {created_by_id})"
        )

        # -- Kategorien --
        categories: list[dict] = []
        for cat in guild.categories:
            categories.append({
                "id": cat.id,
                "name": cat.name,
                "position": cat.position,
                "overwrites": self._serialize_overwrites(cat.overwrites),
            })

        # -- Channels (ohne Kategorien selbst) --
        channels: list[dict] = []
        for ch in guild.channels:
            if isinstance(ch, discord.CategoryChannel):
                continue
            ch_data: dict[str, Any] = {
                "id": ch.id,
                "name": ch.name,
                "type": self._channel_type_str(ch.type),
                "position": ch.position,
                "category_id": ch.category_id,
                "overwrites": self._serialize_overwrites(ch.overwrites),
            }
            # Textkanal-spezifische Felder
            if isinstance(ch, discord.TextChannel):
                ch_data["topic"] = ch.topic
                ch_data["slowmode_delay"] = ch.slowmode_delay
                ch_data["nsfw"] = ch.nsfw
            # Voice-spezifische Felder
            if isinstance(ch, discord.VoiceChannel):
                ch_data["bitrate"] = ch.bitrate
                ch_data["user_limit"] = ch.user_limit
            # Forum-spezifische Felder
            if isinstance(ch, discord.ForumChannel):
                ch_data["topic"] = ch.topic
                ch_data["slowmode_delay"] = ch.slowmode_delay
                ch_data["nsfw"] = ch.nsfw
            channels.append(ch_data)

        # -- Rollen (ohne @everyone und managed Rollen wie Bot-Rollen) --
        roles: list[dict] = []
        for role in guild.roles:
            if role.is_default():
                continue
            if role.managed:
                continue
            roles.append({
                "id": role.id,
                "name": role.name,
                "color": role.color.value,
                "position": role.position,
                "permissions": role.permissions.value,
                "hoist": role.hoist,
                "mentionable": role.mentionable,
            })

        # -- Server-Einstellungen --
        settings: dict[str, Any] = {
            "name": guild.name,
            "icon_url": str(guild.icon.url) if guild.icon else None,
            "banner_url": str(guild.banner.url) if guild.banner else None,
            "verification_level": str(guild.verification_level),
            "default_notifications": str(guild.default_notifications),
            "afk_channel_id": guild.afk_channel.id if guild.afk_channel else None,
            "afk_channel_name": guild.afk_channel.name if guild.afk_channel else None,
            "afk_timeout": guild.afk_timeout,
            "system_channel_id": (
                guild.system_channel.id if guild.system_channel else None
            ),
            "system_channel_name": (
                guild.system_channel.name if guild.system_channel else None
            ),
        }

        # -- Emojis --
        emojis: list[dict] = []
        for emoji in guild.emojis:
            emojis.append({
                "id": emoji.id,
                "name": emoji.name,
                "animated": emoji.animated,
            })

        # -- Welcome-Screen-Einstellungen (sofern vorhanden) --
        welcome_settings: dict[str, Any] = {}
        if guild.welcome_screen:
            ws = guild.welcome_screen
            welcome_settings["description"] = ws.description
            welcome_settings["channels"] = []
            for wc in ws.welcome_channels:
                welcome_settings["channels"].append({
                    "channel_id": wc.channel.id,
                    "channel_name": wc.channel.name,
                    "description": wc.description,
                    "emoji": str(wc.emoji) if wc.emoji else None,
                })

        # -- Backup zusammenstellen --
        backup = {
            "backup_id": backup_id,
            "guild_id": guild.id,
            "guild_name": guild.name,
            "created_at": now.isoformat(),
            "created_by": created_by_id,
            "categories": categories,
            "channels": channels,
            "roles": roles,
            "settings": settings,
            "emojis": emojis,
            "welcome_settings": welcome_settings,
        }

        self._save_backup(backup)

        logger.info(
            f"Backup {backup_id} erstellt: "
            f"{len(categories)} Kategorien, {len(channels)} Channels, "
            f"{len(roles)} Rollen, {len(emojis)} Emojis"
        )

        return backup

    # ------------------------------------------------------------------
    # Backups auflisten
    # ------------------------------------------------------------------

    async def list_backups(self, guild_id: int) -> list[dict]:
        """
        Alle Backups fuer eine bestimmte Guild auflisten.

        Gibt nur Zusammenfassungen zurueck (kein vollstaendiger Inhalt).

        Args:
            guild_id: Discord-Guild-ID

        Returns:
            Liste von Backup-Zusammenfassungen, sortiert nach Erstelldatum (neueste zuerst)
        """
        summaries: list[dict] = []

        for path in BACKUP_DIR.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if data.get("guild_id") != guild_id:
                    continue

                summaries.append({
                    "backup_id": data["backup_id"],
                    "guild_name": data.get("guild_name", "Unbekannt"),
                    "created_at": data.get("created_at", ""),
                    "created_by": data.get("created_by", 0),
                    "categories_count": len(data.get("categories", [])),
                    "channels_count": len(data.get("channels", [])),
                    "roles_count": len(data.get("roles", [])),
                    "emojis_count": len(data.get("emojis", [])),
                })
            except (json.JSONDecodeError, IOError, KeyError) as e:
                logger.warning(f"Backup-Datei lesen fehlgeschlagen ({path.name}): {e}")
                continue

        # Nach Erstelldatum sortieren (neueste zuerst)
        summaries.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        return summaries

    # ------------------------------------------------------------------
    # Einzelnes Backup laden
    # ------------------------------------------------------------------

    async def get_backup(self, backup_id: str) -> Optional[dict]:
        """
        Einzelnes Backup anhand der ID laden.

        Args:
            backup_id: Eindeutige Backup-ID

        Returns:
            Vollstaendiges Backup-Dict oder None wenn nicht gefunden
        """
        return self._load_backup(backup_id)

    # ------------------------------------------------------------------
    # Backup loeschen
    # ------------------------------------------------------------------

    async def delete_backup(self, backup_id: str) -> bool:
        """
        Backup-Datei löschen.

        Args:
            backup_id: Eindeutige Backup-ID

        Returns:
            True wenn erfolgreich gelöscht, False wenn nicht gefunden
        """
        path = self._backup_path(backup_id)
        if not path.exists():
            logger.warning(f"Backup {backup_id} zum Löschen nicht gefunden")
            return False

        try:
            path.unlink()
            logger.info(f"Backup {backup_id} gelöscht")
            return True
        except IOError as e:
            logger.error(f"Backup {backup_id} Löschen fehlgeschlagen: {e}")
            return False

    # ------------------------------------------------------------------
    # Backup mit aktuellem Server vergleichen
    # ------------------------------------------------------------------

    async def compare_backup(
        self, guild: discord.Guild, backup_id: str
    ) -> dict:
        """
        Aktuellen Server-Zustand mit einem Backup vergleichen.

        Ermittelt Unterschiede bei Channels, Rollen und Einstellungen.

        Args:
            guild: Aktueller Discord-Server
            backup_id: ID des zu vergleichenden Backups

        Returns:
            Dict mit Änderungen:
            {
                "found": bool,
                "channels": {"added": [...], "removed": [...], "changed": [...]},
                "roles": {"added": [...], "removed": [...], "changed": [...]},
                "settings": {"changed": [...]},
            }
        """
        backup = self._load_backup(backup_id)
        if not backup:
            return {"found": False}

        result: dict[str, Any] = {"found": True}

        # -- Channels vergleichen --
        backup_channels = {ch["id"]: ch for ch in backup.get("channels", [])}
        backup_categories = {cat["id"]: cat for cat in backup.get("categories", [])}
        # Alle Backup-Channel-IDs (Channels + Kategorien)
        backup_ch_ids = set(backup_channels.keys()) | set(backup_categories.keys())

        current_ch_ids: set[int] = set()
        current_channels: dict[int, discord.abc.GuildChannel] = {}
        for ch in guild.channels:
            current_ch_ids.add(ch.id)
            current_channels[ch.id] = ch

        channels_added: list[str] = []
        channels_removed: list[str] = []
        channels_changed: list[str] = []

        # Neue Channels (im Server, aber nicht im Backup)
        for ch_id in current_ch_ids - backup_ch_ids:
            ch = current_channels[ch_id]
            channels_added.append(f"{ch.name} ({self._channel_type_str(ch.type)})")

        # Entfernte Channels (im Backup, aber nicht im Server)
        for ch_id in backup_ch_ids - current_ch_ids:
            if ch_id in backup_channels:
                ch_data = backup_channels[ch_id]
                channels_removed.append(
                    f"{ch_data['name']} ({ch_data.get('type', 'unbekannt')})"
                )
            elif ch_id in backup_categories:
                cat_data = backup_categories[ch_id]
                channels_removed.append(f"{cat_data['name']} (category)")

        # Geaenderte Channels (Name oder Typ anders)
        for ch_id in current_ch_ids & set(backup_channels.keys()):
            ch = current_channels[ch_id]
            bch = backup_channels[ch_id]
            changes: list[str] = []
            if ch.name != bch["name"]:
                changes.append(f"Name: {bch['name']} -> {ch.name}")
            if hasattr(ch, "topic") and isinstance(ch, discord.TextChannel):
                if (ch.topic or "") != (bch.get("topic") or ""):
                    changes.append("Topic geändert")
            if changes:
                channels_changed.append(f"{ch.name}: {', '.join(changes)}")

        result["channels"] = {
            "added": channels_added,
            "removed": channels_removed,
            "changed": channels_changed,
        }

        # -- Rollen vergleichen --
        backup_roles = {r["id"]: r for r in backup.get("roles", [])}
        backup_role_ids = set(backup_roles.keys())

        current_role_ids: set[int] = set()
        current_roles_map: dict[int, discord.Role] = {}
        for role in guild.roles:
            if role.is_default() or role.managed:
                continue
            current_role_ids.add(role.id)
            current_roles_map[role.id] = role

        roles_added: list[str] = []
        roles_removed: list[str] = []
        roles_changed: list[str] = []

        for r_id in current_role_ids - backup_role_ids:
            roles_added.append(current_roles_map[r_id].name)

        for r_id in backup_role_ids - current_role_ids:
            roles_removed.append(backup_roles[r_id]["name"])

        for r_id in current_role_ids & backup_role_ids:
            role = current_roles_map[r_id]
            brole = backup_roles[r_id]
            changes = []
            if role.name != brole["name"]:
                changes.append(f"Name: {brole['name']} -> {role.name}")
            if role.color.value != brole["color"]:
                changes.append("Farbe geändert")
            if role.permissions.value != brole["permissions"]:
                changes.append("Berechtigungen geändert")
            if role.hoist != brole["hoist"]:
                changes.append(f"Hoist: {brole['hoist']} -> {role.hoist}")
            if role.mentionable != brole["mentionable"]:
                changes.append(
                    f"Mentionable: {brole['mentionable']} -> {role.mentionable}"
                )
            if changes:
                roles_changed.append(f"{role.name}: {', '.join(changes)}")

        result["roles"] = {
            "added": roles_added,
            "removed": roles_removed,
            "changed": roles_changed,
        }

        # -- Einstellungen vergleichen --
        backup_settings = backup.get("settings", {})
        settings_changed: list[str] = []

        if guild.name != backup_settings.get("name"):
            settings_changed.append(
                f"Servername: {backup_settings.get('name')} -> {guild.name}"
            )

        if str(guild.verification_level) != backup_settings.get("verification_level"):
            settings_changed.append(
                f"Verifizierungsstufe: {backup_settings.get('verification_level')} "
                f"-> {guild.verification_level}"
            )

        if str(guild.default_notifications) != backup_settings.get(
            "default_notifications"
        ):
            settings_changed.append(
                f"Standard-Benachrichtigungen: "
                f"{backup_settings.get('default_notifications')} "
                f"-> {guild.default_notifications}"
            )

        if guild.afk_timeout != backup_settings.get("afk_timeout"):
            settings_changed.append(
                f"AFK-Timeout: {backup_settings.get('afk_timeout')} "
                f"-> {guild.afk_timeout}"
            )

        current_afk = guild.afk_channel.name if guild.afk_channel else None
        backup_afk = backup_settings.get("afk_channel_name")
        if current_afk != backup_afk:
            settings_changed.append(
                f"AFK-Channel: {backup_afk or 'Keiner'} -> {current_afk or 'Keiner'}"
            )

        current_sys = guild.system_channel.name if guild.system_channel else None
        backup_sys = backup_settings.get("system_channel_name")
        if current_sys != backup_sys:
            settings_changed.append(
                f"System-Channel: {backup_sys or 'Keiner'} "
                f"-> {current_sys or 'Keiner'}"
            )

        result["settings"] = {"changed": settings_changed}

        return result

    # ------------------------------------------------------------------
    # Backup wiederherstellen
    # ------------------------------------------------------------------

    async def restore_backup(
        self,
        guild: discord.Guild,
        backup_id: str,
        mode: str = "full",
    ) -> dict:
        """
        Server-Struktur aus einem Backup wiederherstellen.

        ACHTUNG: Der Modus 'full' ist destruktiv und loescht/erstellt
        Channels und Rollen neu!

        Args:
            guild: Discord-Server
            backup_id: ID des Backups
            mode: Wiederherstellungsmodus
                  - "full": Alles wiederherstellen (gefährlich!)
                  - "roles_only": Nur Rollen wiederherstellen
                  - "channels_only": Nur Channels wiederherstellen
                  - "add_missing": Nur fehlende Elemente hinzufügen

        Returns:
            Ergebnis-Dict mit Zusammenfassung der durchgeführten Aktionen
        """
        backup = self._load_backup(backup_id)
        if not backup:
            return {"success": False, "error": "Backup nicht gefunden"}

        results: dict[str, Any] = {
            "success": True,
            "mode": mode,
            "roles_created": 0,
            "roles_deleted": 0,
            "roles_errors": [],
            "channels_created": 0,
            "channels_deleted": 0,
            "channels_errors": [],
            "categories_created": 0,
            "categories_deleted": 0,
            "settings_updated": [],
            "settings_errors": [],
        }

        logger.info(
            f"Starte Backup-Wiederherstellung {backup_id} "
            f"fuer '{guild.name}' (Modus: {mode})"
        )

        # Lock: Nur ein Restore gleichzeitig pro Manager
        async with self._restore_lock:
            try:
                # -- Rollen wiederherstellen --
                if mode in ("full", "roles_only", "add_missing"):
                    await self._restore_roles(guild, backup, mode, results)

                # -- Channels/Kategorien wiederherstellen --
                if mode in ("full", "channels_only", "add_missing"):
                    await self._restore_channels(guild, backup, mode, results)

                # -- Einstellungen wiederherstellen (nur im full-Modus) --
                if mode == "full":
                    await self._restore_settings(guild, backup, results)

            except Exception as e:
                logger.error(
                    f"Kritischer Fehler bei Wiederherstellung {backup_id}: {e}",
                    exc_info=True,
                )
                results["success"] = False
                results["error"] = str(e)

        logger.info(
            f"Wiederherstellung {backup_id} abgeschlossen: "
            f"{results['roles_created']} Rollen erstellt, "
            f"{results['roles_deleted']} gelöscht, "
            f"{results['channels_created']} Channels erstellt, "
            f"{results['channels_deleted']} gelöscht, "
            f"{results['categories_created']} Kategorien erstellt"
        )

        return results

    async def _restore_roles(
        self,
        guild: discord.Guild,
        backup: dict,
        mode: str,
        results: dict,
    ) -> None:
        """
        Rollen aus dem Backup wiederherstellen.

        Im 'full'-Modus werden bestehende (nicht-managed) Rollen geloescht.
        Im 'add_missing'-Modus werden nur fehlende Rollen hinzugefuegt.

        Args:
            guild: Discord-Server
            backup: Backup-Daten
            mode: Wiederherstellungsmodus
            results: Ergebnis-Dict (wird in-place modifiziert)
        """
        backup_roles = backup.get("roles", [])
        backup_role_names = {r["name"] for r in backup_roles}
        current_role_names = {
            r.name for r in guild.roles if not r.is_default() and not r.managed
        }

        # Im full-Modus: Rollen löschen die nicht im Backup sind
        if mode == "full":
            for role in guild.roles:
                if role.is_default() or role.managed:
                    continue
                if role.name not in backup_role_names:
                    try:
                        await role.delete(reason=f"Backup-Restore: Rolle nicht im Backup")
                        results["roles_deleted"] += 1
                    except (discord.Forbidden, discord.HTTPException) as e:
                        results["roles_errors"].append(
                            f"Löschen '{role.name}': {e}"
                        )

        # Fehlende Rollen erstellen (sortiert nach Position, niedrigste zuerst)
        roles_to_create = sorted(backup_roles, key=lambda r: r["position"])
        for role_data in roles_to_create:
            if mode == "add_missing" and role_data["name"] in current_role_names:
                continue
            if mode == "full" and role_data["name"] in current_role_names:
                # Rolle existiert schon — Eigenschaften aktualisieren
                existing = discord.utils.get(guild.roles, name=role_data["name"])
                if existing and not existing.managed and not existing.is_default():
                    try:
                        await existing.edit(
                            color=discord.Color(role_data["color"]),
                            permissions=discord.Permissions(role_data["permissions"]),
                            hoist=role_data["hoist"],
                            mentionable=role_data["mentionable"],
                            reason="Backup-Restore: Rolle aktualisiert",
                        )
                    except (discord.Forbidden, discord.HTTPException) as e:
                        results["roles_errors"].append(
                            f"Aktualisieren '{role_data['name']}': {e}"
                        )
                continue

            try:
                await guild.create_role(
                    name=role_data["name"],
                    color=discord.Color(role_data["color"]),
                    permissions=discord.Permissions(role_data["permissions"]),
                    hoist=role_data["hoist"],
                    mentionable=role_data["mentionable"],
                    reason=f"Backup-Restore ({backup['backup_id']})",
                )
                results["roles_created"] += 1
            except (discord.Forbidden, discord.HTTPException) as e:
                results["roles_errors"].append(
                    f"Erstellen '{role_data['name']}': {e}"
                )

    async def _restore_channels(
        self,
        guild: discord.Guild,
        backup: dict,
        mode: str,
        results: dict,
    ) -> None:
        """
        Channels und Kategorien aus dem Backup wiederherstellen.

        Im 'full'-Modus werden bestehende Channels geloescht und neu erstellt.
        Im 'add_missing'-Modus werden nur fehlende Channels hinzugefuegt.

        Args:
            guild: Discord-Server
            backup: Backup-Daten
            mode: Wiederherstellungsmodus
            results: Ergebnis-Dict (wird in-place modifiziert)
        """
        backup_categories = backup.get("categories", [])
        backup_channels = backup.get("channels", [])

        backup_cat_names = {c["name"] for c in backup_categories}
        backup_ch_names = {c["name"] for c in backup_channels}
        current_cat_names = {c.name for c in guild.categories}
        current_ch_names = {
            c.name for c in guild.channels
            if not isinstance(c, discord.CategoryChannel)
        }

        # Im full-Modus: Channels und Kategorien löschen die nicht im Backup sind
        if mode == "full":
            for ch in guild.channels:
                if isinstance(ch, discord.CategoryChannel):
                    if ch.name not in backup_cat_names:
                        try:
                            await ch.delete(
                                reason="Backup-Restore: Kategorie nicht im Backup"
                            )
                            results["categories_deleted"] = (
                                results.get("categories_deleted", 0) + 1
                            )
                        except (discord.Forbidden, discord.HTTPException) as e:
                            results["channels_errors"].append(
                                f"Kategorie löschen '{ch.name}': {e}"
                            )
                else:
                    if ch.name not in backup_ch_names:
                        try:
                            await ch.delete(
                                reason="Backup-Restore: Channel nicht im Backup"
                            )
                            results["channels_deleted"] += 1
                        except (discord.Forbidden, discord.HTTPException) as e:
                            results["channels_errors"].append(
                                f"Channel löschen '{ch.name}': {e}"
                            )

        # Mapping: Alte Kategorie-ID -> neue Kategorie (fuer Channel-Zuordnung)
        category_map: dict[int, discord.CategoryChannel] = {}

        # Kategorien erstellen
        for cat_data in sorted(backup_categories, key=lambda c: c["position"]):
            if mode == "add_missing" and cat_data["name"] in current_cat_names:
                # Existierende Kategorie fuer Mapping merken
                existing = discord.utils.get(guild.categories, name=cat_data["name"])
                if existing:
                    category_map[cat_data["id"]] = existing
                continue

            if mode == "full" and cat_data["name"] in current_cat_names:
                # Im full-Modus bestehende Kategorie wiederverwenden
                existing = discord.utils.get(guild.categories, name=cat_data["name"])
                if existing:
                    category_map[cat_data["id"]] = existing
                continue

            try:
                new_cat = await guild.create_category(
                    name=cat_data["name"],
                    position=cat_data["position"],
                    reason=f"Backup-Restore ({backup['backup_id']})",
                )
                category_map[cat_data["id"]] = new_cat
                results["categories_created"] += 1
            except (discord.Forbidden, discord.HTTPException) as e:
                results["channels_errors"].append(
                    f"Kategorie erstellen '{cat_data['name']}': {e}"
                )

        # Channels erstellen
        for ch_data in sorted(backup_channels, key=lambda c: c["position"]):
            if mode == "add_missing" and ch_data["name"] in current_ch_names:
                continue

            if mode == "full" and ch_data["name"] in current_ch_names:
                continue

            # Ziel-Kategorie ermitteln
            target_category: Optional[discord.CategoryChannel] = None
            if ch_data.get("category_id"):
                target_category = category_map.get(ch_data["category_id"])

            try:
                ch_type = ch_data.get("type", "text")

                if ch_type == "voice":
                    await guild.create_voice_channel(
                        name=ch_data["name"],
                        category=target_category,
                        position=ch_data.get("position", 0),
                        bitrate=ch_data.get("bitrate", 64000),
                        user_limit=ch_data.get("user_limit", 0),
                        reason=f"Backup-Restore ({backup['backup_id']})",
                    )
                elif ch_type == "stage":
                    await guild.create_stage_channel(
                        name=ch_data["name"],
                        category=target_category,
                        position=ch_data.get("position", 0),
                        reason=f"Backup-Restore ({backup['backup_id']})",
                    )
                elif ch_type == "forum":
                    await guild.create_forum(
                        name=ch_data["name"],
                        category=target_category,
                        position=ch_data.get("position", 0),
                        topic=ch_data.get("topic"),
                        slowmode_delay=ch_data.get("slowmode_delay", 0),
                        nsfw=ch_data.get("nsfw", False),
                        reason=f"Backup-Restore ({backup['backup_id']})",
                    )
                else:
                    # text, news und andere als Textkanal erstellen
                    await guild.create_text_channel(
                        name=ch_data["name"],
                        category=target_category,
                        position=ch_data.get("position", 0),
                        topic=ch_data.get("topic"),
                        slowmode_delay=ch_data.get("slowmode_delay", 0),
                        nsfw=ch_data.get("nsfw", False),
                        reason=f"Backup-Restore ({backup['backup_id']})",
                    )

                results["channels_created"] += 1

            except (discord.Forbidden, discord.HTTPException) as e:
                results["channels_errors"].append(
                    f"Channel erstellen '{ch_data['name']}' ({ch_type}): {e}"
                )

    async def _restore_settings(
        self,
        guild: discord.Guild,
        backup: dict,
        results: dict,
    ) -> None:
        """
        Server-Einstellungen aus dem Backup wiederherstellen.

        Aktualisiert Servername, Verifizierungsstufe, Benachrichtigungen,
        AFK-Einstellungen und System-Channel.

        Args:
            guild: Discord-Server
            backup: Backup-Daten
            results: Ergebnis-Dict (wird in-place modifiziert)
        """
        settings = backup.get("settings", {})
        if not settings:
            return

        kwargs: dict[str, Any] = {}

        # Servername
        if settings.get("name") and guild.name != settings["name"]:
            kwargs["name"] = settings["name"]
            results["settings_updated"].append(
                f"Servername: {guild.name} -> {settings['name']}"
            )

        # Verifizierungsstufe
        backup_vl = settings.get("verification_level")
        if backup_vl:
            vl_map = {
                "none": discord.VerificationLevel.none,
                "low": discord.VerificationLevel.low,
                "medium": discord.VerificationLevel.medium,
                "high": discord.VerificationLevel.high,
                "highest": discord.VerificationLevel.highest,
            }
            # discord.py gibt z.B. "VerificationLevel.medium" oder "medium" zurueck
            vl_key = backup_vl.replace("VerificationLevel.", "").lower()
            if vl_key in vl_map:
                kwargs["verification_level"] = vl_map[vl_key]
                results["settings_updated"].append(
                    f"Verifizierungsstufe: {vl_key}"
                )

        # AFK-Timeout
        if settings.get("afk_timeout") and guild.afk_timeout != settings["afk_timeout"]:
            kwargs["afk_timeout"] = settings["afk_timeout"]
            results["settings_updated"].append(
                f"AFK-Timeout: {settings['afk_timeout']}s"
            )

        # AFK-Channel
        afk_name = settings.get("afk_channel_name")
        if afk_name:
            afk_ch = discord.utils.get(guild.voice_channels, name=afk_name)
            if afk_ch:
                kwargs["afk_channel"] = afk_ch
                results["settings_updated"].append(f"AFK-Channel: {afk_name}")

        # System-Channel
        sys_name = settings.get("system_channel_name")
        if sys_name:
            sys_ch = discord.utils.get(guild.text_channels, name=sys_name)
            if sys_ch:
                kwargs["system_channel"] = sys_ch
                results["settings_updated"].append(f"System-Channel: {sys_name}")

        if kwargs:
            try:
                await guild.edit(
                    reason=f"Backup-Restore ({backup['backup_id']})",
                    **kwargs,
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                results["settings_errors"].append(f"Einstellungen anwenden: {e}")
                logger.error(f"Server-Einstellungen wiederherstellen fehlgeschlagen: {e}")
