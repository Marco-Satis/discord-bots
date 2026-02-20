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


def _default_config() -> dict[str, Any]:
    """Standard-Konfiguration fuer das Temp-Voice-System zurueckgeben."""
    return {
        "join_channel_id": None,
        "category_id": None,
        "default_limit": 0,
        "afk_timeout_minutes": 5,
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

        # In Daten speichern
        self._channels[str(channel.id)] = {
            "owner_id": member.id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "name": channel_name,
            "user_limit": default_limit,
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

    @property
    def config(self) -> dict[str, Any]:
        """Aktuelle Konfiguration zurueckgeben (Kopie)."""
        return dict(self._config)
