"""
Async Minecraft RCON Client
Implementiert das Minecraft RCON-Protokoll ueber TCP Sockets.

Protokoll-Referenz: https://wiki.vg/RCON
"""

import asyncio
import struct
from typing import Optional
from utils.logger import get_logger

logger = get_logger("minecraft.rcon")

# Maximale RCON-Paketgroesse (Minecraft: 4096 Bytes Payload)
MAX_PACKET_SIZE = 4110


class MinecraftRCON:
    """
    Async Minecraft RCON Client.

    RCON-Paketformat (alle Felder Little-Endian signed 32-bit):
        [4 Bytes: length] [4 Bytes: request_id] [4 Bytes: type]
        [N Bytes: payload] [1 Byte: null] [1 Byte: pad null]

    length = Gesamtgroesse nach dem Length-Feld (request_id + type + payload + 2 nulls)
    type: 3=Login, 2=Command, 0=Response
    request_id: Bei Auth-Fehler sendet der Server -1
    """

    PACKET_TYPE_LOGIN = 3
    PACKET_TYPE_COMMAND = 2
    PACKET_TYPE_RESPONSE = 0

    def __init__(self, host: str, port: int, password: str, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._request_id_counter = 0
        self._authenticated = False

    def _next_request_id(self) -> int:
        """Naechste Request-ID (einfacher Zaehler)"""
        self._request_id_counter += 1
        if self._request_id_counter > 2_000_000_000:
            self._request_id_counter = 1
        return self._request_id_counter

    def _build_packet(self, req_id: int, packet_type: int, payload: str) -> bytes:
        """RCON-Paket erstellen"""
        payload_encoded = payload.encode('utf-8') + b'\x00'  # Null-terminierter String

        # Length = 4 (req_id) + 4 (type) + len(payload+null) + 1 (pad null)
        length = 4 + 4 + len(payload_encoded) + 1

        # Alles als signed 32-bit Little-Endian (Protokoll-Spezifikation)
        packet = struct.pack('<i', length)
        packet += struct.pack('<i', req_id)
        packet += struct.pack('<i', packet_type)
        packet += payload_encoded
        packet += b'\x00'  # Pad-Null

        return packet

    async def _send_packet(self, req_id: int, packet_type: int, payload: str) -> None:
        """RCON-Paket senden"""
        if not self.writer:
            raise RuntimeError("Nicht mit RCON-Server verbunden")

        packet = self._build_packet(req_id, packet_type, payload)
        self.writer.write(packet)
        await self.writer.drain()

    async def _receive_packet(self, timeout: Optional[float] = None) -> tuple[int, int, str]:
        """
        RCON-Paket empfangen.

        Args:
            timeout: Optionaler Timeout (Standard: self.timeout)

        Returns:
            (request_id, packet_type, payload)
        """
        if not self.reader:
            raise RuntimeError("Nicht mit RCON-Server verbunden")

        recv_timeout = timeout if timeout is not None else self.timeout

        # Length-Feld lesen (4 Bytes, signed int)
        length_data = await asyncio.wait_for(
            self.reader.readexactly(4),
            timeout=recv_timeout
        )
        length = struct.unpack('<i', length_data)[0]

        # Sanity-Check: Length muss positiv und innerhalb der Grenzen sein
        if length < 10 or length > MAX_PACKET_SIZE:
            raise RuntimeError(f"Ungueltiges RCON-Paket: length={length}")

        # Restliches Paket lesen (exakt 'length' Bytes)
        packet_data = await asyncio.wait_for(
            self.reader.readexactly(length),
            timeout=recv_timeout
        )

        # Parsen: request_id (signed), type (signed), payload (ohne Null-Terminatoren)
        req_id = struct.unpack('<i', packet_data[0:4])[0]
        pkt_type = struct.unpack('<i', packet_data[4:8])[0]
        # Payload: alles nach type bis zu den zwei abschliessenden Null-Bytes
        payload = packet_data[8:-2].decode('utf-8', errors='replace')

        return req_id, pkt_type, payload

    # ------------------------------------------------------------------
    # Verbindung
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Verbindung herstellen und authentifizieren"""
        try:
            logger.debug(f"RCON-Verbindung zu {self.host}:{self.port}")

            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout
            )

            # Login-Paket senden
            login_id = self._next_request_id()
            await self._send_packet(login_id, self.PACKET_TYPE_LOGIN, self.password)

            # Login-Antwort empfangen
            resp_id, resp_type, _ = await self._receive_packet()

            # Auth-Fehler: Server antwortet mit request_id == -1
            if resp_id == -1 or resp_id != login_id:
                await self.close()
                logger.error("RCON-Authentifizierung fehlgeschlagen")
                return False

            self._authenticated = True
            logger.info(f"RCON authentifiziert: {self.host}:{self.port}")
            return True

        except Exception as e:
            logger.error(f"RCON-Verbindungsfehler: {e}")
            await self.close()
            return False

    async def reconnect(self) -> bool:
        """Verbindung trennen und neu aufbauen"""
        logger.debug("RCON-Reconnect...")
        await self.close()
        return await self.connect()

    # ------------------------------------------------------------------
    # Befehle
    # ------------------------------------------------------------------

    async def command(self, cmd: str) -> str:
        """
        RCON-Befehl ausfuehren und Antwort zurueckgeben.

        Liest die erste Antwort mit normalem Timeout, danach weitere
        Pakete mit kurzem Timeout (Multi-Packet-Antworten). Begrenzt
        auf max. 64 Pakete um Endlos-Schleifen zu verhindern.

        Args:
            cmd: Befehlsstring (z.B. "list", "say Hallo")

        Returns:
            Antworttext des Servers
        """
        if not self._authenticated or not self.writer:
            raise RuntimeError("Nicht mit RCON-Server authentifiziert")

        cmd_id = self._next_request_id()
        await self._send_packet(cmd_id, self.PACKET_TYPE_COMMAND, cmd)

        response_parts: list[str] = []
        max_packets = 64

        # Erstes Paket mit normalem Timeout lesen
        resp_id, resp_type, resp_payload = await self._receive_packet()
        if resp_id == cmd_id and resp_type == self.PACKET_TYPE_RESPONSE:
            response_parts.append(resp_payload)

        # Weitere Pakete mit kurzem Timeout (Multi-Packet-Antworten)
        short_timeout = min(0.5, self.timeout)
        for _ in range(max_packets - 1):
            try:
                resp_id, resp_type, resp_payload = await self._receive_packet(
                    timeout=short_timeout
                )
                if resp_id == cmd_id and resp_type == self.PACKET_TYPE_RESPONSE:
                    if not resp_payload:
                        break  # Leeres Paket = Ende
                    response_parts.append(resp_payload)
                else:
                    break  # Anderes Paket, fertig
            except (asyncio.TimeoutError, RuntimeError):
                break  # Timeout = keine weiteren Pakete

        return ''.join(response_parts).strip()

    # ------------------------------------------------------------------
    # Verbindung schliessen
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """RCON-Verbindung schliessen"""
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception as e:
                logger.debug(f"RCON close Fehler: {e}")

        self.reader = None
        self.writer = None
        self._authenticated = False

    async def __aenter__(self):
        """Context-Manager Eintritt — verbindet automatisch"""
        success = await self.connect()
        if not success:
            raise RuntimeError(
                f"RCON-Verbindung zu {self.host}:{self.port} fehlgeschlagen"
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context-Manager Austritt — schliesst Verbindung"""
        await self.close()
