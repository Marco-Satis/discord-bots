"""
TeamSpeak ServerQuery Client — Wrapper um die ts3-Bibliothek.

Stellt alle gaengigen ServerQuery-Operationen bereit und nutzt
asyncio.to_thread() fuer nicht-blockierende Ausfuehrung.
Automatische Reconnect-Logik bei Verbindungsverlust.

Voraussetzung: Das Paket ``ts3`` (pip install ts3) muss installiert sein.
Falls nicht vorhanden, wird TS3_AVAILABLE auf False gesetzt und
alle Methoden geben sinnvolle Fehlermeldungen zurueck.
"""

import asyncio
from typing import Optional

from utils.logger import get_logger

logger = get_logger("modules.teamspeak.client")

# ts3-Bibliothek ist optional — graceful degradation
try:
    import ts3
    TS3_AVAILABLE = True
except ImportError:
    ts3 = None
    TS3_AVAILABLE = False
    logger.warning("ts3-Paket nicht installiert. TeamSpeak-Integration deaktiviert.")


class TSClient:
    """Wrapper fuer die ts3 ServerQuery-Verbindung.

    Alle oeffentlichen Methoden sind async-kompatibel (via asyncio.to_thread).
    Bei Verbindungsverlust wird automatisch ein Reconnect versucht.
    """

    # Maximale Reconnect-Versuche bevor aufgegeben wird
    MAX_RECONNECT_ATTEMPTS = 3
    # Wartezeit zwischen Reconnect-Versuchen in Sekunden
    RECONNECT_DELAY = 5

    def __init__(
        self,
        host: str,
        port: int = 10011,
        user: str = "serveradmin",
        password: str = "",
        server_id: int = 1,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.server_id = server_id

        self._connection: Optional[object] = None  # ts3.query.TS3ServerConnection
        self._lock = asyncio.Lock()
        self._connected = False

    # ------------------------------------------------------------------
    # Verbindungsverwaltung
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Verbindung zum TeamSpeak-Server herstellen.

        Returns:
            True bei Erfolg, False bei Fehler.
        """
        if not TS3_AVAILABLE:
            logger.error("ts3-Paket nicht verfuegbar — Verbindung nicht moeglich.")
            return False

        try:
            self._connection = await asyncio.to_thread(self._sync_connect)
            self._connected = True
            logger.info(
                f"Verbindung zu TeamSpeak hergestellt: {self.host}:{self.port} "
                f"(VServer {self.server_id})"
            )
            return True
        except Exception as e:
            self._connected = False
            logger.error(f"TeamSpeak-Verbindung fehlgeschlagen: {e}")
            return False

    def _sync_connect(self):
        """Synchrone Verbindungsherstellung (wird in Thread ausgefuehrt)."""
        conn = ts3.query.TS3ServerConnection(f"{self.host}:{self.port}")
        conn.login(
            client_login_name=self.user,
            client_login_password=self.password,
        )
        conn.use(sid=self.server_id)
        return conn

    async def disconnect(self) -> None:
        """Verbindung zum TeamSpeak-Server trennen."""
        if self._connection is not None:
            try:
                await asyncio.to_thread(self._sync_disconnect)
            except Exception as e:
                logger.debug(f"Fehler beim Trennen der TS-Verbindung: {e}")
            finally:
                self._connection = None
                self._connected = False
                logger.info("TeamSpeak-Verbindung getrennt.")

    def _sync_disconnect(self):
        """Synchrones Trennen (wird in Thread ausgefuehrt)."""
        if self._connection is not None:
            try:
                self._connection.quit()
            except Exception:
                pass

    def is_connected(self) -> bool:
        """Prueft ob eine aktive Verbindung besteht."""
        return self._connected and self._connection is not None

    async def _ensure_connection(self) -> bool:
        """Stellt sicher, dass eine Verbindung besteht. Reconnect bei Bedarf.

        Returns:
            True wenn verbunden, False wenn Reconnect fehlschlug.
        """
        if self.is_connected():
            # Verbindung mit einem einfachen Befehl testen
            try:
                await asyncio.to_thread(self._sync_whoami)
                return True
            except Exception:
                logger.warning("TeamSpeak-Verbindung verloren, versuche Reconnect...")
                self._connected = False

        # Reconnect-Versuche
        for attempt in range(1, self.MAX_RECONNECT_ATTEMPTS + 1):
            logger.info(
                f"TeamSpeak Reconnect-Versuch {attempt}/{self.MAX_RECONNECT_ATTEMPTS}..."
            )
            success = await self.connect()
            if success:
                logger.info("TeamSpeak Reconnect erfolgreich.")
                return True
            if attempt < self.MAX_RECONNECT_ATTEMPTS:
                await asyncio.sleep(self.RECONNECT_DELAY)

        logger.error("TeamSpeak Reconnect endgueltig fehlgeschlagen.")
        return False

    def _sync_whoami(self):
        """Einfacher Verbindungstest (synchron)."""
        self._connection.whoami()

    # ------------------------------------------------------------------
    # Hilfsmethode fuer geschuetzte Ausfuehrung
    # ------------------------------------------------------------------

    async def _execute(self, func, *args, **kwargs):
        """Fuehrt eine synchrone TS3-Funktion thread-safe aus.

        Stellt vorher sicher, dass die Verbindung steht.
        Bei Fehler wird ein Reconnect versucht und der Befehl wiederholt.

        Args:
            func: Synchrone Funktion, die ausgefuehrt werden soll.
            *args, **kwargs: Argumente fuer die Funktion.

        Returns:
            Rueckgabewert der Funktion oder None bei Fehler.
        """
        async with self._lock:
            if not await self._ensure_connection():
                return None

            try:
                return await asyncio.to_thread(func, *args, **kwargs)
            except Exception as e:
                logger.warning(f"TS3-Befehl fehlgeschlagen: {e}, versuche Reconnect...")
                # Ein Reconnect-Versuch
                if await self._ensure_connection():
                    try:
                        return await asyncio.to_thread(func, *args, **kwargs)
                    except Exception as retry_err:
                        logger.error(f"TS3-Befehl nach Reconnect erneut fehlgeschlagen: {retry_err}")
                        return None
                return None

    # ------------------------------------------------------------------
    # Server-Informationen
    # ------------------------------------------------------------------

    async def get_server_info(self) -> dict:
        """Ruft Server-Informationen ab (Name, Version, Uptime, Slots).

        Returns:
            dict mit Feldern: name, version, uptime, max_clients,
            clients_online, channels_online. Leeres dict bei Fehler.
        """
        result = await self._execute(self._sync_get_server_info)
        return result if result else {}

    def _sync_get_server_info(self) -> dict:
        """Synchrone Server-Info-Abfrage."""
        resp = self._connection.serverinfo()
        info = resp.parsed[0] if resp.parsed else {}
        return {
            "name": info.get("virtualserver_name", "Unbekannt"),
            "version": info.get("virtualserver_version", "Unbekannt"),
            "uptime": int(info.get("virtualserver_uptime", 0)),
            "max_clients": int(info.get("virtualserver_maxclients", 0)),
            "clients_online": int(info.get("virtualserver_clientsonline", 0)),
            "channels_online": int(info.get("virtualserver_channelsonline", 0)),
            "platform": info.get("virtualserver_platform", "Unbekannt"),
        }

    async def get_client_list(self) -> list:
        """Ruft die Liste der online verbundenen Clients ab.

        Filtert ServerQuery-Clients heraus.

        Returns:
            Liste von dicts mit: clid, client_nickname, cid (Channel-ID),
            client_idle_time, client_type. Leere Liste bei Fehler.
        """
        result = await self._execute(self._sync_get_client_list)
        return result if result else []

    def _sync_get_client_list(self) -> list:
        """Synchrone Client-Liste."""
        resp = self._connection.clientlist()
        clients = []
        for entry in resp.parsed:
            # client_type=1 sind ServerQuery-Clients — herausfiltern
            if int(entry.get("client_type", 0)) == 1:
                continue
            clients.append({
                "clid": int(entry.get("clid", 0)),
                "client_nickname": entry.get("client_nickname", "Unbekannt"),
                "cid": int(entry.get("cid", 0)),
                "client_idle_time": int(entry.get("client_idle_time", 0)),
                "client_type": int(entry.get("client_type", 0)),
            })
        return clients

    async def get_channel_list(self) -> list:
        """Ruft alle Channels des Servers ab.

        Returns:
            Liste von dicts mit: cid, channel_name, pid (Parent-ID),
            total_clients. Leere Liste bei Fehler.
        """
        result = await self._execute(self._sync_get_channel_list)
        return result if result else []

    def _sync_get_channel_list(self) -> list:
        """Synchrone Channel-Liste."""
        resp = self._connection.channellist()
        channels = []
        for entry in resp.parsed:
            channels.append({
                "cid": int(entry.get("cid", 0)),
                "channel_name": entry.get("channel_name", "Unbekannt"),
                "pid": int(entry.get("pid", 0)),
                "total_clients": int(entry.get("total_clients", 0)),
            })
        return channels

    # ------------------------------------------------------------------
    # Client-Verwaltung
    # ------------------------------------------------------------------

    async def kick_client(self, client_id: int, reason: str = "") -> bool:
        """Kickt einen Client vom Server.

        Args:
            client_id: Die Client-ID (clid) des zu kickenden Benutzers.
            reason: Optionaler Kick-Grund.

        Returns:
            True bei Erfolg, False bei Fehler.
        """
        result = await self._execute(self._sync_kick_client, client_id, reason)
        if result:
            logger.info(f"Client {client_id} gekickt. Grund: {reason or 'Kein Grund'}")
        return result is not None

    def _sync_kick_client(self, client_id: int, reason: str):
        """Synchroner Kick (reasonid=5 = kick vom Server)."""
        self._connection.clientkick(
            reasonid=5,
            clid=client_id,
            reasonmsg=reason[:80] if reason else "",
        )
        return True

    async def ban_client(
        self, client_id: int, duration_seconds: int = 0, reason: str = ""
    ) -> bool:
        """Bannt einen Client.

        Args:
            client_id: Die Client-ID (clid).
            duration_seconds: Ban-Dauer in Sekunden (0 = permanent).
            reason: Optionaler Ban-Grund.

        Returns:
            True bei Erfolg, False bei Fehler.
        """
        result = await self._execute(
            self._sync_ban_client, client_id, duration_seconds, reason
        )
        if result:
            dauer_text = f"{duration_seconds}s" if duration_seconds > 0 else "permanent"
            logger.info(
                f"Client {client_id} gebannt ({dauer_text}). Grund: {reason or 'Kein Grund'}"
            )
        return result is not None

    def _sync_ban_client(self, client_id: int, duration_seconds: int, reason: str):
        """Synchroner Ban."""
        self._connection.banclient(
            clid=client_id,
            time=duration_seconds,
            banreason=reason[:80] if reason else "",
        )
        return True

    async def unban(self, ban_id: int) -> bool:
        """Hebt einen Ban anhand der Ban-ID auf.

        Args:
            ban_id: Die Ban-ID.

        Returns:
            True bei Erfolg, False bei Fehler.
        """
        result = await self._execute(self._sync_unban, ban_id)
        if result:
            logger.info(f"Ban {ban_id} aufgehoben.")
        return result is not None

    def _sync_unban(self, ban_id: int):
        """Synchrones Unban."""
        self._connection.bandel(banid=ban_id)
        return True

    async def get_ban_list(self) -> list:
        """Ruft die aktuelle Ban-Liste ab.

        Returns:
            Liste von dicts mit: banid, ip, name, uid, reason, duration,
            created, invoker_name. Leere Liste bei Fehler oder keinen Bans.
        """
        result = await self._execute(self._sync_get_ban_list)
        return result if result else []

    def _sync_get_ban_list(self) -> list:
        """Synchrone Ban-Liste."""
        try:
            resp = self._connection.banlist()
            bans = []
            for entry in resp.parsed:
                bans.append({
                    "banid": int(entry.get("banid", 0)),
                    "ip": entry.get("ip", ""),
                    "name": entry.get("name", ""),
                    "uid": entry.get("uid", ""),
                    "reason": entry.get("reason", ""),
                    "duration": int(entry.get("duration", 0)),
                    "created": int(entry.get("created", 0)),
                    "invoker_name": entry.get("invokername", ""),
                })
            return bans
        except Exception as e:
            # Kein Ban vorhanden gibt oft einen Fehler zurueck
            if "empty result" in str(e).lower() or "1281" in str(e):
                return []
            raise

    async def poke_client(self, client_id: int, message: str) -> bool:
        """Sendet eine Poke-Nachricht an einen Client.

        Args:
            client_id: Die Client-ID (clid).
            message: Die Nachricht (max. 100 Zeichen).

        Returns:
            True bei Erfolg, False bei Fehler.
        """
        result = await self._execute(self._sync_poke_client, client_id, message)
        if result:
            logger.info(f"Client {client_id} gepoked: {message[:50]}")
        return result is not None

    def _sync_poke_client(self, client_id: int, message: str):
        """Synchroner Poke."""
        self._connection.clientpoke(
            clid=client_id,
            msg=message[:100],
        )
        return True

    async def move_client(self, client_id: int, channel_id: int) -> bool:
        """Verschiebt einen Client in einen anderen Channel.

        Args:
            client_id: Die Client-ID (clid).
            channel_id: Die Ziel-Channel-ID (cid).

        Returns:
            True bei Erfolg, False bei Fehler.
        """
        result = await self._execute(self._sync_move_client, client_id, channel_id)
        if result:
            logger.info(f"Client {client_id} in Channel {channel_id} verschoben.")
        return result is not None

    def _sync_move_client(self, client_id: int, channel_id: int):
        """Synchrones Verschieben."""
        self._connection.clientmove(clid=client_id, cid=channel_id)
        return True

    async def send_text_message(
        self, target_mode: int, target_id: int, message: str
    ) -> bool:
        """Sendet eine Textnachricht.

        Args:
            target_mode: 1 = Client (privat), 2 = Channel, 3 = Server.
            target_id: Ziel-ID (clid, cid, oder sid je nach Modus).
            message: Die Nachricht.

        Returns:
            True bei Erfolg, False bei Fehler.
        """
        result = await self._execute(
            self._sync_send_text_message, target_mode, target_id, message
        )
        return result is not None

    def _sync_send_text_message(self, target_mode: int, target_id: int, message: str):
        """Synchrones Senden einer Textnachricht."""
        self._connection.sendtextmessage(
            targetmode=target_mode,
            target=target_id,
            msg=message[:1024],
        )
        return True

    # ------------------------------------------------------------------
    # Channel-Verwaltung
    # ------------------------------------------------------------------

    async def create_channel(self, name: str, parent_id: int = 0, **kwargs) -> int:
        """Erstellt einen neuen Channel.

        Args:
            name: Name des Channels.
            parent_id: ID des Parent-Channels (0 = Top-Level).
            **kwargs: Weitere Channel-Eigenschaften (z.B. channel_flag_temporary).

        Returns:
            Die Channel-ID des neuen Channels, oder 0 bei Fehler.
        """
        result = await self._execute(
            self._sync_create_channel, name, parent_id, **kwargs
        )
        if result:
            logger.info(f"Channel erstellt: '{name}' (ID: {result}, Parent: {parent_id})")
        return result if result else 0

    def _sync_create_channel(self, name: str, parent_id: int, **kwargs) -> int:
        """Synchrone Channel-Erstellung."""
        params = {
            "channel_name": name,
            "cpid": parent_id,
        }
        params.update(kwargs)
        resp = self._connection.channelcreate(**params)
        if resp.parsed:
            return int(resp.parsed[0].get("cid", 0))
        return 0

    async def delete_channel(self, channel_id: int, force: bool = True) -> bool:
        """Loescht einen Channel.

        Args:
            channel_id: Die Channel-ID.
            force: Wenn True, werden Clients vorher verschoben.

        Returns:
            True bei Erfolg, False bei Fehler.
        """
        result = await self._execute(self._sync_delete_channel, channel_id, force)
        if result:
            logger.info(f"Channel {channel_id} geloescht.")
        return result is not None

    def _sync_delete_channel(self, channel_id: int, force: bool):
        """Synchrones Channel-Loeschen."""
        self._connection.channeldelete(cid=channel_id, force=1 if force else 0)
        return True
