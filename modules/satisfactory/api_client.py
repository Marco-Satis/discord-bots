"""
Satisfactory Dedicated Server HTTPS API Client
Docs: https://satisfactory.wiki.gg/wiki/Dedicated_servers/HTTPS_API
"""

import ssl
import asyncio
import aiohttp
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from utils.logger import get_logger

logger = get_logger("satisfactory.api")


@dataclass
class ServerState:
    """Parsed server state from API"""
    active_session: str = ""
    num_players: int = 0
    player_limit: int = 0
    tech_tier: int = 0
    game_phase: str = ""
    game_duration: float = 0.0
    is_paused: bool = False
    average_tick_rate: float = 0.0


@dataclass
class HealthInfo:
    """Server health information"""
    health: str = "unknown"
    server_custom_data: str = ""


class SatisfactoryAPIError(Exception):
    """API request error"""
    pass


class SatisfactoryAPI:
    """
    Async client for the Satisfactory Dedicated Server HTTPS API

    The API uses POST requests with a JSON body containing:
    - function: The API function name
    - data: The request payload
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 7777,
                 token: Optional[str] = None, verify_ssl: bool = False, timeout: int = 10) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.verify_ssl = verify_ssl
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.base_url = f"https://{host}:{port}/api/v1"

        # SSL context for self-signed certs
        # Note: Satisfactory Dedicated Server uses self-signed certificates by default
        # Set verify_ssl=False to allow connections to the local API without cert validation
        self._ssl = ssl.create_default_context()
        if not verify_ssl:
            self._ssl.check_hostname = False
            self._ssl.verify_mode = ssl.CERT_NONE

        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._session is None or self._session.closed:
                conn = aiohttp.TCPConnector(ssl=self._ssl)
                self._session = aiohttp.ClientSession(
                    timeout=self.timeout, connector=conn
                )
            return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, function: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send API request with retry logic"""
        session = await self._get_session()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        payload = {"function": function}
        if data:
            payload["data"] = data

        last_error = None
        for attempt in range(3):
            try:
                async with session.post(
                    self.base_url,
                    json=payload,
                    headers=headers,
                    ssl=self._ssl
                ) as resp:
                    if resp.status == 200 or resp.status == 204:
                        if resp.content_length and resp.content_length > 0:
                            return await resp.json()
                        return {}
                    else:
                        text = await resp.text()
                        raise SatisfactoryAPIError(
                            f"HTTP {resp.status}: {text[:200]}"
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                if attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1))
                    # Reset session on connection errors
                    await self.close()
                    self._session = None
                continue

        raise SatisfactoryAPIError(f"API request failed after 3 attempts: {last_error}")

    # ------------------------------------------------------------------
    # API Functions
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthInfo:
        """Check server health (no auth required)"""
        try:
            data = await self._request("HealthCheck")
            return HealthInfo(
                health=data.get("data", {}).get("health", "unknown"),
                server_custom_data=data.get("data", {}).get("serverCustomData", "")
            )
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            return HealthInfo(health="offline")

    async def query_server_state(self) -> ServerState:
        """Get current server state"""
        try:
            data = await self._request("QueryServerState")
            state = data.get("data", {}).get("serverGameState", {})
            return ServerState(
                active_session=state.get("activeSessionName", ""),
                num_players=state.get("numConnectedPlayers", 0),
                player_limit=state.get("playerLimit", 0),
                tech_tier=state.get("techTier", 0),
                game_phase=state.get("gamePhase", ""),
                game_duration=state.get("totalGameDuration", 0.0),
                is_paused=state.get("isGamePaused", False),
                average_tick_rate=state.get("averageTickRate", 0.0)
            )
        except SatisfactoryAPIError as e:
            logger.error(f"Query state API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Query state failed: {e}")
            return ServerState()

    async def get_server_options(self) -> Dict[str, Any]:
        """Get server settings"""
        try:
            data = await self._request("GetServerOptions")
            return data.get("data", {}).get("serverOptions", {})
        except Exception as e:
            logger.error(f"Get options failed: {e}")
            return {}

    async def get_advanced_game_settings(self) -> Tuple[bool, Dict[str, Any]]:
        """Get advanced game settings. Returns (creative_mode_enabled, settings_dict)"""
        try:
            data = await self._request("GetAdvancedGameSettings")
            return data.get("data", {}).get("creativeModeEnabled", False), \
                   data.get("data", {}).get("advancedGameSettings", {})
        except Exception as e:
            logger.error(f"Get advanced settings failed: {e}")
            return False, {}

    async def apply_server_options(self, options: Dict[str, Any]) -> bool:
        """Apply server options (requires admin token)"""
        try:
            await self._request("ApplyServerOptions", {"updatedServerOptions": options})
            return True
        except Exception as e:
            logger.error(f"Apply options failed: {e}")
            return False

    async def save_game(self, save_name: str = "") -> bool:
        """Trigger server save"""
        try:
            await self._request("SaveGame", {"SaveName": save_name})
            logger.info(f"Game saved: {save_name or 'auto'}")
            return True
        except Exception as e:
            logger.error(f"Save game failed: {e}")
            return False

    async def run_command(self, command: str) -> str:
        """Execute server console command"""
        try:
            data = await self._request("RunCommand", {"command": command})
            return data.get("data", {}).get("commandResult", "")
        except Exception as e:
            logger.error(f"Run command failed: {e}")
            return f"Error: {e}"

    async def shutdown(self) -> bool:
        """Request graceful server shutdown"""
        try:
            await self._request("Shutdown")
            return True
        except Exception as e:
            logger.error(f"Shutdown request failed: {e}")
            return False

    async def is_online(self) -> bool:
        """Quick check if server API is reachable"""
        try:
            health = await self.health_check()
            return health.health != "offline"
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.debug(f"is_online check failed: {e}")
            return False
