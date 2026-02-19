"""
Async Minecraft RCON Client
Implements Minecraft RCON protocol over TCP sockets
"""

import asyncio
import struct
from typing import Optional
from utils.logger import get_logger

logger = get_logger("minecraft.rcon")


class MinecraftRCON:
    """
    Async Minecraft RCON client using raw sockets.

    RCON Protocol (Minecraft):
    - Packet format: length(4) + requestId(4) + type(4) + payload + null(2)
    - Length: 4-byte int (size of rest of packet, excluding length field)
    - RequestId: 4-byte int (for matching responses)
    - Type: 4-byte int (3=Login, 2=Command, 0=Response)
    - Payload: null-terminated string
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
        """Get next request ID (simple counter)"""
        self._request_id_counter += 1
        return self._request_id_counter

    def _build_packet(self, req_id: int, packet_type: int, payload: str) -> bytes:
        """Build RCON packet bytes"""
        # Encode payload as null-terminated string
        payload_bytes = payload.encode('utf-8') + b'\x00'

        # Calculate length: 4 (req_id) + 4 (type) + payload + 1 (trailing null)
        length = 4 + 4 + len(payload_bytes)

        # Pack: length + req_id + type + payload + trailing null
        packet = struct.pack('<I', length)  # length
        packet += struct.pack('<I', req_id)  # request ID
        packet += struct.pack('<I', packet_type)  # packet type
        packet += payload_bytes  # payload + null terminator
        packet += b'\x00'  # extra null terminator

        return packet

    async def _send_packet(self, req_id: int, packet_type: int, payload: str) -> None:
        """Send RCON packet"""
        if not self.writer:
            raise RuntimeError("Not connected to RCON server")

        packet = self._build_packet(req_id, packet_type, payload)
        self.writer.write(packet)
        await self.writer.drain()

    async def _receive_packet(self) -> tuple[int, int, str]:
        """
        Receive RCON packet
        Returns: (request_id, packet_type, payload)
        """
        if not self.reader:
            raise RuntimeError("Not connected to RCON server")

        try:
            # Read length (4 bytes)
            length_data = await asyncio.wait_for(
                self.reader.readexactly(4),
                timeout=self.timeout
            )
            length = struct.unpack('<I', length_data)[0]

            # Read rest of packet
            packet_data = await asyncio.wait_for(
                self.reader.readexactly(length + 2),  # +2 for trailing nulls
                timeout=self.timeout
            )

            # Parse packet
            req_id = struct.unpack('<I', packet_data[0:4])[0]
            pkt_type = struct.unpack('<I', packet_data[4:8])[0]
            payload = packet_data[8:-2].decode('utf-8', errors='replace')

            return req_id, pkt_type, payload

        except asyncio.TimeoutError:
            raise RuntimeError(f"RCON receive timeout ({self.timeout}s)")
        except Exception as e:
            raise RuntimeError(f"RCON packet parsing error: {e}")

    async def connect(self) -> bool:
        """Connect and authenticate to RCON server"""
        try:
            logger.debug(f"Connecting to RCON {self.host}:{self.port}")

            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout
            )

            # Send login packet
            login_id = self._next_request_id()
            await self._send_packet(login_id, self.PACKET_TYPE_LOGIN, self.password)

            # Receive login response
            resp_id, resp_type, resp_payload = await self._receive_packet()

            if resp_type != self.PACKET_TYPE_RESPONSE or resp_id != login_id:
                await self.close()
                logger.error("RCON authentication failed: Invalid response type")
                return False

            self._authenticated = True
            logger.info(f"RCON authenticated: {self.host}:{self.port}")
            return True

        except Exception as e:
            logger.error(f"RCON connect error: {e}")
            await self.close()
            return False

    async def command(self, cmd: str) -> str:
        """
        Execute RCON command and get response

        Args:
            cmd: Command string to execute

        Returns:
            Command response text
        """
        if not self._authenticated or not self.writer:
            raise RuntimeError("Not authenticated to RCON server")

        try:
            cmd_id = self._next_request_id()

            # Send command packet
            await self._send_packet(cmd_id, self.PACKET_TYPE_COMMAND, cmd)

            # Receive response - may be multiple packets
            response_parts = []
            while True:
                resp_id, resp_type, resp_payload = await self._receive_packet()

                # Check if this is our response
                if resp_id == cmd_id and resp_type == self.PACKET_TYPE_RESPONSE:
                    response_parts.append(resp_payload)

                    # Empty response indicates end of multi-packet response
                    if not resp_payload:
                        break

            return ''.join(response_parts).strip()

        except Exception as e:
            logger.error(f"RCON command error ({cmd}): {e}")
            raise

    async def close(self) -> None:
        """Close RCON connection"""
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception as e:
                logger.debug(f"RCON close error: {e}")

        self.reader = None
        self.writer = None
        self._authenticated = False

    async def __aenter__(self):
        """Context manager entry"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        await self.close()
