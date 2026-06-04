"""
Temp Voice Manager — Phase 12a (F17)
Verwaltung temporaerer Sprachkanaele mit Join-to-Create Mechanik.

Wenn ein Mitglied den konfigurierten "Join-to-Create" Voice-Channel
betritt, wird automatisch ein temporaerer Voice-Channel erstellt.
Der Ersteller ist Owner und kann den Channel ueber Buttons steuern.
Leere temporaere Channels werden nach kurzer Verzoegerung geloescht.

Persistenz:
  - Channel-Daten:  data/admin/temp_voice.json
  - Konfiguration:  data/admin/temp_voice_config.json

Datenformat (temp_voice.json):
{
  "channel_id_str": {
    "owner_id": int,
    "created_at": "ISO-Timestamp",
    "name": "Kanalname",
    "user_limit": int
  }
}

Konfiguration (temp_voice_config.json):
{
  "join_channel_id": int | null,
  "category_id": int | null,
  "default_limit": 0,
  "afk_timeout_minutes": 5
}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord

from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR

logger = get_logger("modules.temp_voice")

# Standard-Pfade
DATA_FILE = ADMIN_DATA_DIR / "temp_voice.json"
CONFIG_FILE = ADMIN_DATA_DIR / "temp_voice_config.json"

# Ringpuffer-Groesse fuer Join/Leave-Events pro Channel (Panel zeigt letzte ~8)
_EVENT_CAP = 10


def _now_iso() -> str:
    """Aktuellen UTC-Zeitstempel als ISO-String."""
    return datetime.now(timezone.utc).isoformat()


def _default_config() -> dict[str, Any]:
    """Standard-Konfiguration fuer das Temp-Voice-System zurueckgeben."""
    return {
        "join_channel_id": None,
        "category_id": None,
        "default_limit": 0,
        "afk_timeout_minutes": 5,
        # Interface-Kanal (VOICEPANEL-Style): ein persistentes Steuer-Panel in
        # einem festen Text-Channel. Steuert den Temp-Channel in dem der Klickende
        # gerade ist (Buttons resolven via member.voice.channel, nicht panel-bound).
        "interface_channel_id": None,
        "interface_message_id": None,
    }


class TempVoiceManager:
    """
    Verwaltet temporaere Voice-Channels mit Persistenz.

    Funktionen:
    - Temporaere Channels erstellen und loeschen
    - Ownership-Verwaltung (Ersteller = Owner)
    - Ownership-Transfer an andere Mitglieder
    - Join-to-Create und Kategorie konfigurieren
    - AFK-Timeout-Einstellung

    Der Manager kuemmert sich um die Datenverwaltung und
    Channel-Erstellung. Das Event-Handling (Voice-State-Updates)
    und die interaktiven Buttons werden vom Cog uebernommen.
    """

    def __init__(
        self,
        data_file: Path | None = None,
        config_file: Path | None = None,
    ) -> None:
        """
        Args:
            data_file: Pfad zur Channel-Daten-JSON (Standard: data/admin/temp_voice.json)
            config_file: Pfad zur Config-JSON (Standard: data/admin/temp_voice_config.json)
        """
        self.data_file = data_file or DATA_FILE
        self.config_file = config_file or CONFIG_FILE
        self._channels: dict[str, dict[str, Any]] = {}
        self._config: dict[str, Any] = _default_config()
        self._load()
        self._load_config()

    # ------------------------------------------------------------------
    # Persistence — Channel-Daten
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Temp-Voice-Daten von Disk laden."""
        try:
            if self.data_file.exists():
                with open(self.data_file, "r", encoding="utf-8") as f:
                    self._channels = json.load(f)
                count = len(self._channels)
                logger.info(f"Temp-Voice-Daten geladen: {count} Channels")
            else:
                logger.info("Keine Temp-Voice-Datei vorhanden, starte leer")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Temp-Voice-Daten laden fehlgeschlagen: {e}")

    def _save(self) -> None:
        """Temp-Voice-Daten auf Disk speichern."""
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self._channels, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Temp-Voice-Daten speichern fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Persistence — Konfiguration
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        """Temp-Voice-Konfiguration von Disk laden."""
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # Defaults ergaenzen falls Keys fehlen
                defaults = _default_config()
                for key, value in defaults.items():
                    loaded.setdefault(key, value)
                self._config = loaded
                logger.info("Temp-Voice-Konfiguration geladen")
            else:
                logger.info(
                    "Keine Temp-Voice-Config vorhanden, verwende Standardwerte"
                )
                self._save_config()
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Temp-Voice-Config laden fehlgeschlagen: {e}")

    def _save_config(self) -> None:
        """Temp-Voice-Konfiguration auf Disk speichern."""
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Temp-Voice-Config speichern fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Channel erstellen
    # ------------------------------------------------------------------

    async def create_channel(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> discord.VoiceChannel:
        """
        Temporaeren Voice-Channel fuer ein Mitglied erstellen.

        Erstellt den Channel in der konfigurierten Kategorie mit
        Berechtigungen, die dem Ersteller volle Kontrolle geben.

        Args:
            guild: Discord-Server
            member: Mitglied das den Channel erstellt (wird Owner)

        Returns:
            Der erstellte Voice-Channel

        Raises:
            discord.Forbidden: Bot hat keine Berechtigung
            discord.HTTPException: Discord-API-Fehler
        """
        # Kategorie ermitteln
        category = None
        cat_id = self._config.get("category_id")
        if cat_id:
            category = guild.get_channel(cat_id)
            if category and not isinstance(category, discord.CategoryChannel):
                category = None

        # Channel-Name
        channel_name = f"{member.display_name}'s Channel"

        # Standard-Userlimit aus Config
        default_limit = self._config.get("default_limit", 0)

        # Berechtigungen: Owner bekommt Manage-Rechte
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                connect=True,
                speak=True,
            ),
            member: discord.PermissionOverwrite(
                connect=True,
                speak=True,
                manage_channels=True,
                mute_members=True,
                deafen_members=True,
                move_members=True,
                manage_permissions=True,
            ),
            guild.me: discord.PermissionOverwrite(
                connect=True,
                speak=True,
                manage_channels=True,
                move_members=True,
                manage_permissions=True,
            ),
        }

        # Channel erstellen
        channel = await guild.create_voice_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            user_limit=default_limit,
            reason=f"Temp-Voice-Channel fuer {member.display_name}",
        )

        # In Daten speichern (inkl. VOICEPANEL-State: private/banned/joined_at/events)
        now = _now_iso()
        self._channels[str(channel.id)] = {
            "owner_id": member.id,
            "created_at": now,
            "name": channel_name,
            "user_limit": default_limit,
            "private": False,              # offen by default (Spec)
            "banned": [],                  # pro Channel, geloescht wenn Channel weg
            "joined_at": {str(member.id): now},  # Zeit-im-Channel fuer Slot-Liste
            "events": [{"uid": member.id, "type": "join", "ts": now}],  # Ringpuffer
        }
        self._save()

        logger.info(
            f"Temp-Voice-Channel erstellt: #{channel.name} (ID: {channel.id}) "
            f"fuer {member.display_name}"
        )
        return channel

    # ------------------------------------------------------------------
    # Channel loeschen
    # ------------------------------------------------------------------

    def delete_channel(self, channel_id: int) -> bool:
        """
        Temporaeren Channel aus den Daten entfernen.

        Entfernt nur den Eintrag aus der Persistenz.
        Das tatsaechliche Loeschen des Discord-Channels muss
        separat durch den Cog erfolgen.

        Args:
            channel_id: Discord-Channel-ID

        Returns:
            True wenn der Channel entfernt wurde, False wenn nicht gefunden
        """
        cid = str(channel_id)
        if cid in self._channels:
            del self._channels[cid]
            self._save()
            logger.info(f"Temp-Voice-Channel aus Daten entfernt: {channel_id}")
            return True
        return False

    # ------------------------------------------------------------------
    # Abfragen
    # ------------------------------------------------------------------

    def is_temp_channel(self, channel_id: int) -> bool:
        """
        Pruefen ob ein Channel ein temporaerer Voice-Channel ist.

        Args:
            channel_id: Discord-Channel-ID

        Returns:
            True wenn es ein Temp-Channel ist
        """
        return str(channel_id) in self._channels

    def get_owner(self, channel_id: int) -> int | None:
        """
        Owner-ID eines temporaeren Channels zurueckgeben.

        Args:
            channel_id: Discord-Channel-ID

        Returns:
            Owner-User-ID oder None wenn Channel nicht gefunden
        """
        data = self._channels.get(str(channel_id))
        if data:
            return data.get("owner_id")
        return None

    def get_all_channels(self) -> dict[str, dict[str, Any]]:
        """
        Alle registrierten temporaeren Channels zurueckgeben.

        Returns:
            Dict mit channel_id_str als Key und Channel-Daten als Value (Kopie)
        """
        return {k: dict(v) for k, v in self._channels.items()}

    # ------------------------------------------------------------------
    # Ownership-Transfer
    # ------------------------------------------------------------------

    def transfer_ownership(self, channel_id: int, new_owner_id: int) -> bool:
        """
        Ownership eines temporaeren Channels uebertragen.

        Args:
            channel_id: Discord-Channel-ID
            new_owner_id: Discord-User-ID des neuen Owners

        Returns:
            True wenn erfolgreich, False wenn Channel nicht gefunden
        """
        cid = str(channel_id)
        if cid not in self._channels:
            return False

        old_owner = self._channels[cid].get("owner_id")
        self._channels[cid]["owner_id"] = new_owner_id
        self._save()

        logger.info(
            f"Temp-Voice Ownership transferiert: Channel {channel_id} "
            f"von {old_owner} zu {new_owner_id}"
        )
        return True

    # ------------------------------------------------------------------
    # Channel-Daten aktualisieren
    # ------------------------------------------------------------------

    def update_channel_name(self, channel_id: int, name: str) -> bool:
        """
        Namen eines temporaeren Channels in den Daten aktualisieren.

        Args:
            channel_id: Discord-Channel-ID
            name: Neuer Kanalname

        Returns:
            True wenn erfolgreich, False wenn Channel nicht gefunden
        """
        cid = str(channel_id)
        if cid not in self._channels:
            return False

        self._channels[cid]["name"] = name
        self._save()
        return True

    def update_user_limit(self, channel_id: int, user_limit: int) -> bool:
        """
        Userlimit eines temporaeren Channels in den Daten aktualisieren.

        Args:
            channel_id: Discord-Channel-ID
            user_limit: Neues Userlimit (0 = unbegrenzt)

        Returns:
            True wenn erfolgreich, False wenn Channel nicht gefunden
        """
        cid = str(channel_id)
        if cid not in self._channels:
            return False

        self._channels[cid]["user_limit"] = user_limit
        self._save()
        return True

    # ------------------------------------------------------------------
    # VOICEPANEL-State — Daten-Helper (sync, kein Discord-Call)
    # ------------------------------------------------------------------

    def _ch(self, channel_id: int) -> dict[str, Any] | None:
        """Internen Channel-Datensatz holen (oder None)."""
        return self._channels.get(str(channel_id))

    def _set_flag(self, channel_id: int, key: str, value: Any) -> None:
        """Ein Feld im Channel-Datensatz setzen + speichern."""
        data = self._ch(channel_id)
        if data is not None:
            data[key] = value
            self._save()

    def is_private(self, channel_id: int) -> bool:
        """Ob der Channel als privat (zu + unsichtbar) markiert ist."""
        data = self._ch(channel_id)
        return bool(data and data.get("private", False))

    def get_banned(self, channel_id: int) -> list[int]:
        """Gebannte User-IDs dieses Channels (Kopie)."""
        data = self._ch(channel_id)
        return list(data.get("banned", [])) if data else []

    def is_banned(self, channel_id: int, user_id: int) -> bool:
        """Ob ein User in diesem Channel gebannt ist."""
        return user_id in self.get_banned(channel_id)

    def add_ban(self, channel_id: int, user_id: int) -> None:
        """User zur Ban-Liste hinzufuegen (idempotent) + speichern."""
        data = self._ch(channel_id)
        if data is None:
            return
        banned = data.setdefault("banned", [])
        if user_id not in banned:
            banned.append(user_id)
            self._save()

    def remove_ban(self, channel_id: int, user_id: int) -> bool:
        """User aus der Ban-Liste entfernen. True wenn entfernt."""
        data = self._ch(channel_id)
        if data is None:
            return False
        banned = data.setdefault("banned", [])
        if user_id in banned:
            banned.remove(user_id)
            self._save()
            return True
        return False

    def get_joined_at(self, channel_id: int) -> dict[str, str]:
        """Map User-ID(str) -> Join-Zeitstempel (ISO) fuer die Slot-Liste."""
        data = self._ch(channel_id)
        return dict(data.get("joined_at", {})) if data else {}

    def record_join(self, channel_id: int, user_id: int) -> None:
        """Join eines Users im Channel vermerken (joined_at + Event, idempotent)."""
        data = self._ch(channel_id)
        if data is None:
            return
        joined = data.setdefault("joined_at", {})
        if str(user_id) in joined:
            return  # schon vermerkt (z.B. Owner beim Create) — kein Doppel-Event
        joined[str(user_id)] = _now_iso()
        self.log_event(channel_id, user_id, "join")  # speichert

    def record_leave(self, channel_id: int, user_id: int) -> None:
        """Leave eines Users im Channel vermerken (joined_at-Cleanup + Event)."""
        data = self._ch(channel_id)
        if data is None:
            return
        data.setdefault("joined_at", {}).pop(str(user_id), None)
        self.log_event(channel_id, user_id, "leave")  # speichert

    def log_event(self, channel_id: int, user_id: int, event_type: str) -> None:
        """Join/Leave/Mod-Event in den Ringpuffer schreiben (cap _EVENT_CAP)."""
        data = self._ch(channel_id)
        if data is None:
            return
        events = data.setdefault("events", [])
        events.append({"uid": user_id, "type": event_type, "ts": _now_iso()})
        if len(events) > _EVENT_CAP:
            del events[: len(events) - _EVENT_CAP]
        self._save()

    def get_events(self, channel_id: int, limit: int = 8) -> list[dict[str, Any]]:
        """Letzte `limit` Events des Channels (neueste zuletzt)."""
        data = self._ch(channel_id)
        if not data:
            return []
        events = data.get("events", [])
        return list(events[-limit:])

    def set_panel_message(self, channel_id: int, message_id: int) -> None:
        """Message-ID des Kontrollpanels merken (fuer Slot-Liste-Refresh)."""
        self._set_flag(channel_id, "panel_message_id", message_id)

    def get_panel_message(self, channel_id: int) -> int | None:
        """Message-ID des Kontrollpanels zurueckgeben (oder None)."""
        data = self._ch(channel_id)
        return data.get("panel_message_id") if data else None

    # ------------------------------------------------------------------
    # VOICEPANEL-State — Action-Helper (async, mit Discord-Call)
    # Zentrale Logik statt in den Buttons (Buttons bleiben duenn).
    # ------------------------------------------------------------------

    async def set_private(self, channel: discord.VoiceChannel) -> None:
        """Channel privat schalten: @everyone connect=False + unsichtbar."""
        guild = channel.guild
        try:
            await channel.set_permissions(
                guild.default_role, connect=False, view_channel=False,
                reason="Temp-Voice: privat",
            )
        except discord.HTTPException as e:
            logger.error(f"set_private fehlgeschlagen ({channel.id}): {e}")
            return
        self._set_flag(channel.id, "private", True)
        owner = self.get_owner(channel.id) or 0
        self.log_event(channel.id, owner, "private")

    async def set_public(self, channel: discord.VoiceChannel) -> None:
        """Channel oeffentlich schalten: @everyone connect=True + sichtbar."""
        guild = channel.guild
        try:
            await channel.set_permissions(
                guild.default_role, connect=True, view_channel=True,
                reason="Temp-Voice: oeffentlich",
            )
        except discord.HTTPException as e:
            logger.error(f"set_public fehlgeschlagen ({channel.id}): {e}")
            return
        self._set_flag(channel.id, "private", False)
        owner = self.get_owner(channel.id) or 0
        self.log_event(channel.id, owner, "public")

    async def ban_user(
        self, channel: discord.VoiceChannel, member: discord.Member
    ) -> None:
        """User bannen: aus Channel trennen + connect-deny + in Ban-Liste."""
        # 1. Trennen falls aktuell im Channel
        try:
            if (member.voice and member.voice.channel
                    and member.voice.channel.id == channel.id):
                await member.move_to(None, reason="Temp-Voice: gebannt")
        except discord.HTTPException as e:
            logger.warning(f"Ban-Disconnect fehlgeschlagen ({member.id}): {e}")
        # 2. connect verweigern (sichtbar bleibt, kann aber nicht rein)
        try:
            await channel.set_permissions(
                member, connect=False, reason="Temp-Voice: gebannt",
            )
        except discord.HTTPException as e:
            logger.error(f"Ban-Deny fehlgeschlagen ({member.id}): {e}")
            return
        # 3. State
        self.add_ban(channel.id, member.id)
        self.log_event(channel.id, member.id, "ban")

    async def unban_user(
        self, channel: discord.VoiceChannel, member: discord.Member
    ) -> bool:
        """Ban aufheben: Overwrite entfernen + aus Ban-Liste. True wenn war gebannt."""
        try:
            await channel.set_permissions(
                member, overwrite=None, reason="Temp-Voice: entbannt",
            )
        except discord.HTTPException as e:
            logger.error(f"Unban fehlgeschlagen ({member.id}): {e}")
            return False
        removed = self.remove_ban(channel.id, member.id)
        if removed:
            self.log_event(channel.id, member.id, "unban")
        return removed

    async def claim(
        self, channel: discord.VoiceChannel, member: discord.Member
    ) -> bool:
        """Channel uebernehmen (Owner weg): Ownership + Manage-Rechte an member."""
        if not self.transfer_ownership(channel.id, member.id):
            return False
        try:
            await channel.set_permissions(
                member, connect=True, manage_channels=True,
                move_members=True, manage_permissions=True,
                reason="Temp-Voice: Claim",
            )
        except discord.HTTPException as e:
            logger.error(f"Claim-Perms fehlgeschlagen ({member.id}): {e}")
        self.log_event(channel.id, member.id, "claim")
        return True

    # ------------------------------------------------------------------
    # Konfiguration
    # ------------------------------------------------------------------

    @property
    def join_channel_id(self) -> int | None:
        """Join-to-Create Channel-ID zurueckgeben."""
        return self._config.get("join_channel_id")

    @property
    def category_id(self) -> int | None:
        """Kategorie-ID fuer neue Temp-Channels zurueckgeben."""
        return self._config.get("category_id")

    @property
    def default_limit(self) -> int:
        """Standard-Userlimit fuer neue Channels zurueckgeben."""
        return self._config.get("default_limit", 0)

    @property
    def afk_timeout_minutes(self) -> int:
        """AFK-Timeout in Minuten zurueckgeben."""
        return self._config.get("afk_timeout_minutes", 5)

    def set_join_channel(self, channel_id: int) -> None:
        """
        Join-to-Create Channel konfigurieren.

        Args:
            channel_id: Discord-Voice-Channel-ID
        """
        self._config["join_channel_id"] = channel_id
        self._save_config()
        logger.info(f"Join-to-Create Channel gesetzt: {channel_id}")

    def set_category(self, category_id: int) -> None:
        """
        Kategorie fuer neue Temp-Channels konfigurieren.

        Args:
            category_id: Discord-Kategorie-Channel-ID
        """
        self._config["category_id"] = category_id
        self._save_config()
        logger.info(f"Temp-Voice Kategorie gesetzt: {category_id}")

    def set_default_limit(self, limit: int) -> None:
        """
        Standard-Userlimit fuer neue Channels setzen.

        Args:
            limit: Userlimit (0 = unbegrenzt, 1-99)
        """
        self._config["default_limit"] = max(0, min(99, limit))
        self._save_config()
        logger.info(f"Standard-Userlimit gesetzt: {limit}")

    def set_afk_timeout(self, minutes: int) -> None:
        """
        AFK-Timeout setzen (Minuten bis leerer Channel geloescht wird).

        Args:
            minutes: Timeout in Minuten (1-60)
        """
        self._config["afk_timeout_minutes"] = max(1, min(60, minutes))
        self._save_config()
        logger.info(f"AFK-Timeout gesetzt: {minutes} Minuten")

    # ------------------------------------------------------------------
    # Interface-Kanal (persistentes Steuer-Panel)
    # ------------------------------------------------------------------

    @property
    def interface_channel_id(self) -> int | None:
        """Text-Channel-ID des persistenten Interface-Panels."""
        return self._config.get("interface_channel_id")

    @property
    def interface_message_id(self) -> int | None:
        """Message-ID des Interface-Panels (zum Wiederfinden nach Restart)."""
        return self._config.get("interface_message_id")

    def set_interface_channel(self, channel_id: int) -> None:
        """Interface-Kanal setzen. Loescht eine evtl. alte Message-ID."""
        self._config["interface_channel_id"] = channel_id
        self._config["interface_message_id"] = None
        self._save_config()
        logger.info(f"Temp-Voice Interface-Kanal gesetzt: {channel_id}")

    def set_interface_message(self, message_id: int | None) -> None:
        """Message-ID des geposteten Interface-Panels merken."""
        self._config["interface_message_id"] = message_id
        self._save_config()

    def clear_interface(self) -> None:
        """Interface-Kanal-Konfiguration entfernen."""
        self._config["interface_channel_id"] = None
        self._config["interface_message_id"] = None
        self._save_config()
        logger.info("Temp-Voice Interface-Kanal entfernt")

    @property
    def config(self) -> dict[str, Any]:
        """Aktuelle Konfiguration zurueckgeben (Kopie)."""
        return dict(self._config)
