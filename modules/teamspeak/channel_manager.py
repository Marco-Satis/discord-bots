"""
TeamSpeak Channel-Manager — Automatische Channel-Verwaltung.

Erstellt und löscht Channels basierend auf Gameserver-Status.
Konfiguration wird in data/admin/ts_channels.json gespeichert.
"""

import json
from pathlib import Path
from typing import Optional

from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR

logger = get_logger("modules.teamspeak.channel_manager")

# Konfigurationsdatei für Channel-Mappings
TS_CHANNELS_FILE = ADMIN_DATA_DIR / "ts_channels.json"


class TSChannelManager:
    """Verwaltet TeamSpeak-Channels automatisch.

    Kann Channels erstellen/löschen und bietet automatische
    Game-Channel-Verwaltung basierend auf Gameserver-Starts/-Stops.
    """

    def __init__(self, ts_client):
        """Initialisiert den Channel-Manager.

        Args:
            ts_client: TSClient-Instanz für TS-Operationen.
        """
        self.ts_client = ts_client
        self._config = self._load_config()

    # ------------------------------------------------------------------
    # Konfiguration
    # ------------------------------------------------------------------

    def _load_config(self) -> dict:
        """Lädt die Channel-Konfiguration aus der JSON-Datei.

        Returns:
            dict mit der Konfiguration. Standard-Struktur bei Fehler.
        """
        try:
            if TS_CHANNELS_FILE.exists():
                with open(TS_CHANNELS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.debug(f"Channel-Konfiguration geladen: {TS_CHANNELS_FILE}")
                return data
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Fehler beim Laden der Channel-Konfiguration: {e}")

        return self._default_config()

    def _save_config(self) -> None:
        """Speichert die aktuelle Konfiguration in die JSON-Datei."""
        try:
            ADMIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(TS_CHANNELS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            logger.debug(f"Channel-Konfiguration gespeichert: {TS_CHANNELS_FILE}")
        except IOError as e:
            logger.error(f"Fehler beim Speichern der Channel-Konfiguration: {e}")

    @staticmethod
    def _default_config() -> dict:
        """Gibt die Standard-Konfiguration zurück.

        Returns:
            dict mit leerer Standard-Konfiguration.
        """
        return {
            # Parent-Channel-ID unter dem Game-Channels erstellt werden
            "game_channels_parent_id": 0,
            # Mapping: game_name -> channel_id (für aktive Game-Channels)
            "active_game_channels": {},
            # Template-Einstellungen fuer automatisch erstellte Channels
            "channel_template": {
                "channel_flag_semi_permanent": 1,
                "channel_codec": 4,        # Opus Voice
                "channel_codec_quality": 6,
            },
            # Manuell verwaltete Channels (Name -> Channel-ID)
            "managed_channels": {},
        }

    # ------------------------------------------------------------------
    # Channel-Verwaltung
    # ------------------------------------------------------------------

    async def create_channel(
        self, name: str, parent_id: int = 0, **kwargs
    ) -> int:
        """Erstellt einen neuen TeamSpeak-Channel.

        Args:
            name: Name des neuen Channels.
            parent_id: Parent-Channel-ID (0 = Top-Level).
            **kwargs: Weitere Channel-Eigenschaften für den TS-Server
                      (z.B. channel_flag_temporary, channel_codec_quality).

        Returns:
            Die Channel-ID des erstellten Channels, oder 0 bei Fehler.
        """
        if not self.ts_client.is_connected():
            logger.error("Channel-Erstellung fehlgeschlagen: Keine TS-Verbindung.")
            return 0

        # Template-Einstellungen als Basis verwenden
        channel_params = dict(self._config.get("channel_template", {}))
        channel_params.update(kwargs)

        try:
            channel_id = await self.ts_client.create_channel(
                name=name,
                parent_id=parent_id,
                **channel_params,
            )

            if channel_id > 0:
                # In verwaltete Channels aufnehmen
                self._config["managed_channels"][name] = channel_id
                self._save_config()
                logger.info(f"Channel erstellt: '{name}' (ID: {channel_id})")
            else:
                logger.error(f"Channel-Erstellung fehlgeschlagen für '{name}'.")

            return channel_id

        except Exception as e:
            logger.error(f"Fehler beim Erstellen des Channels '{name}': {e}")
            return 0

    async def delete_channel(self, channel_id: int) -> bool:
        """Löscht einen TeamSpeak-Channel.

        Args:
            channel_id: Die Channel-ID des zu löschenden Channels.

        Returns:
            True bei Erfolg, False bei Fehler.
        """
        if not self.ts_client.is_connected():
            logger.error("Channel-Löschung fehlgeschlagen: Keine TS-Verbindung.")
            return False

        try:
            success = await self.ts_client.delete_channel(channel_id, force=True)

            if success:
                # Aus Konfiguration entfernen
                managed = self._config.get("managed_channels", {})
                keys_to_remove = [
                    k for k, v in managed.items() if v == channel_id
                ]
                for key in keys_to_remove:
                    del managed[key]

                active = self._config.get("active_game_channels", {})
                keys_to_remove = [
                    k for k, v in active.items() if v == channel_id
                ]
                for key in keys_to_remove:
                    del active[key]

                self._save_config()
                logger.info(f"Channel {channel_id} gelöscht.")

            return success

        except Exception as e:
            logger.error(f"Fehler beim Löschen des Channels {channel_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # Automatische Game-Channel-Verwaltung
    # ------------------------------------------------------------------

    async def auto_create_game_channel(self, game_name: str) -> int:
        """Erstellt automatisch einen Channel wenn ein Gameserver startet.

        Der Channel wird unter dem konfigurierten Parent-Channel erstellt.
        Falls bereits ein Channel für dieses Spiel existiert, wird dessen ID
        zurückgegeben ohne einen neuen zu erstellen.

        Args:
            game_name: Name des Spiels/Gameservers.

        Returns:
            Die Channel-ID, oder 0 bei Fehler.
        """
        # Prüfen ob bereits ein Channel für dieses Spiel existiert
        active_channels = self._config.get("active_game_channels", {})
        if game_name in active_channels:
            existing_id = active_channels[game_name]
            # Prüfen ob der Channel noch existiert
            if await self._channel_exists(existing_id):
                logger.info(
                    f"Game-Channel für '{game_name}' existiert bereits (ID: {existing_id})."
                )
                return existing_id
            else:
                # Channel existiert nicht mehr — Mapping entfernen
                logger.warning(
                    f"Game-Channel {existing_id} für '{game_name}' nicht mehr vorhanden."
                )
                del active_channels[game_name]

        parent_id = self._config.get("game_channels_parent_id", 0)
        channel_name = f"[cspacer] {game_name}"

        channel_id = await self.create_channel(
            name=channel_name,
            parent_id=parent_id,
            channel_flag_semi_permanent=1,
        )

        if channel_id > 0:
            self._config["active_game_channels"][game_name] = channel_id
            self._save_config()
            logger.info(
                f"Game-Channel automatisch erstellt: '{game_name}' (ID: {channel_id})"
            )

        return channel_id

    async def auto_delete_game_channel(self, game_name: str) -> bool:
        """Löscht automatisch einen Game-Channel wenn der Server stoppt.

        Args:
            game_name: Name des Spiels/Gameservers.

        Returns:
            True bei Erfolg, False wenn kein Channel gefunden oder Fehler.
        """
        active_channels = self._config.get("active_game_channels", {})

        if game_name not in active_channels:
            logger.debug(f"Kein aktiver Game-Channel für '{game_name}' gefunden.")
            return False

        channel_id = active_channels[game_name]

        # Prüfen ob noch Clients im Channel sind
        client_count = await self._get_channel_client_count(channel_id)
        if client_count > 0:
            logger.warning(
                f"Game-Channel '{game_name}' (ID: {channel_id}) hat noch "
                f"{client_count} Client(s). Channel wird trotzdem gelöscht."
            )

        success = await self.delete_channel(channel_id)

        if success:
            # Aus aktiven Channels entfernen (wurde schon in delete_channel gemacht,
            # aber sicherheitshalber nochmal prüfen)
            if game_name in self._config.get("active_game_channels", {}):
                del self._config["active_game_channels"][game_name]
                self._save_config()
            logger.info(
                f"Game-Channel automatisch gelöscht: '{game_name}' (ID: {channel_id})"
            )
        else:
            logger.error(
                f"Automatisches Löschen des Game-Channels '{game_name}' "
                f"(ID: {channel_id}) fehlgeschlagen."
            )

        return success

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    async def _channel_exists(self, channel_id: int) -> bool:
        """Prüft ob ein Channel mit der gegebenen ID existiert.

        Args:
            channel_id: Die zu prüfende Channel-ID.

        Returns:
            True wenn der Channel existiert, sonst False.
        """
        try:
            channels = await self.ts_client.get_channel_list()
            return any(ch.get("cid") == channel_id for ch in channels)
        except Exception:
            return False

    async def _get_channel_client_count(self, channel_id: int) -> int:
        """Zählt die Clients in einem bestimmten Channel.

        Args:
            channel_id: Die Channel-ID.

        Returns:
            Anzahl der Clients im Channel, 0 bei Fehler.
        """
        try:
            channels = await self.ts_client.get_channel_list()
            for ch in channels:
                if ch.get("cid") == channel_id:
                    return ch.get("total_clients", 0)
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")
        return 0

    def get_active_game_channels(self) -> dict:
        """Gibt die aktuell aktiven Game-Channels zurück.

        Returns:
            dict mit game_name -> channel_id Mapping.
        """
        return dict(self._config.get("active_game_channels", {}))

    def get_managed_channels(self) -> dict:
        """Gibt alle verwalteten Channels zurück.

        Returns:
            dict mit channel_name -> channel_id Mapping.
        """
        return dict(self._config.get("managed_channels", {}))

    def set_game_channels_parent(self, parent_id: int) -> None:
        """Setzt den Parent-Channel für automatische Game-Channels.

        Args:
            parent_id: Die Channel-ID des Parent-Channels.
        """
        self._config["game_channels_parent_id"] = parent_id
        self._save_config()
        logger.info(f"Game-Channels Parent-ID gesetzt: {parent_id}")
