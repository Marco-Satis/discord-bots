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


# Der Dedicated Server laeuft mit 60 Ticks/s (Marco 2026-08-14; live gemessen
# 57,2 bei leerem Server). Alle Bewertungen der Tick-Rate haengen an diesem
# Sollwert — steht er falsch, ist jede Ampel falsch, und genau das war der Fall:
# die Schwellen waren auf 30 kalibriert, weshalb ein auf die Haelfte
# eingebrochener Server noch gruen anzeigte.
SAT_TICK_SOLL = 60.0
SAT_TICK_WARN = 50.0    # darunter ruckelt es sichtbar (< 83 % des Solls)
SAT_TICK_CRIT = 30.0    # darunter laeuft die Fabrik in halber Geschwindigkeit


def tick_zustand(tick_rate: float) -> str:
    """Ampel-Zustand einer Tick-Rate: "ok" | "warn" | "crit"."""
    if tick_rate >= SAT_TICK_WARN:
        return "ok"
    if tick_rate >= SAT_TICK_CRIT:
        return "warn"
    return "crit"


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
    # False = die Abfrage ist gescheitert und das hier sind Default-Werte.
    # Ohne dieses Feld ist ein degradiertes Ergebnis von "0 Spieler online"
    # nicht zu unterscheiden — genau daran haben Aufrufer den Zaehler auf 0
    # gesetzt, obwohl Spieler drauf waren.
    ok: bool = True


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
        # Verhindert, dass die Portsuche sich selbst erneut anstoesst.
        self._port_gesucht = False
        # Der konfigurierte Port bleibt die Heimatadresse: nach einer
        # Ausweich-Uebernahme wird zuerst wieder hier gesucht.
        self._port_konfiguriert = port
        # Ports der ANDEREN Instanzen. Sie duerfen nie uebernommen werden.
        #
        # Ohne diese Sperre passierte am 19./20.08.2026 folgendes: SAT-2 wich
        # auf 7779 aus, der Client von SAT-1 probte seinen Nachbarport, fand
        # dort die API von SAT-2 und uebernahm sie. Ab da schickte SAT-1 seinen
        # Token an den falschen Server, der ihn zurueckwies — im Protokoll als
        # "Token has expired", was in die Irre fuehrt: der Token war gueltig,
        # nur am falschen Server. Wer diese Menge nicht fuellt, baut den Fehler
        # wieder ein.
        self.fremde_ports: set[int] = set()

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
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        payload = {"function": function}
        if data:
            payload["data"] = data

        last_error = None
        for attempt in range(3):
            try:
                # Session bei jedem Versuch neu holen (nach close/reset ist die alte ungueltig)
                session = await self._get_session()
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
                        # 401 auf einem uebernommenen Port heisst fast immer:
                        # wir reden mit der falschen Instanz. Der Server nennt
                        # das "Token has expired" — der Token ist aber gueltig,
                        # nur am falschen Server. Einmal nach Hause und erneut
                        # versuchen, statt bis zum naechsten Neustart zu klagen.
                        if (resp.status == 401
                                and self.port != self._port_konfiguriert):
                            logger.warning(
                                "HTTP 401 auf Port %s — vermutlich fremde "
                                "Instanz. Zurueck auf den konfigurierten Port %s.",
                                self.port, self._port_konfiguriert)
                            self.port = self._port_konfiguriert
                            self.base_url = (
                                f"https://{self.host}:{self.port}/api/v1")
                            continue
                        raise SatisfactoryAPIError(
                            f"HTTP {resp.status}: {text[:200]}"
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                if attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1))
                    # Reset session on connection errors (M35-Fix: unter
                    # _session_lock, sonst Race mit _get_session → Doppel-close)
                    async with self._session_lock:
                        await self.close()
                        self._session = None
                continue

        # Befund C-20 (Audit 2026-08-18): Der Server bindet seine HTTPS-API
        # NICHT zuverlaessig auf `-Port`. Nach einem systemd-Neustart haengt
        # der alte Socket noch, und die Instanz weicht auf `Port + 1` aus.
        # Belegt im Serverprotokoll von SAT-2:
        #     15.08. manueller Start  -> "Server API listening on …:7778"
        #     16.08. 04:15 Auto-Neustart -> …:7779
        #     16.08. manueller Start  -> …:7778
        #     18.08. 04:14 Auto-Neustart -> …:7779
        # Ein fester Wert in der Konfiguration kann deshalb nie dauerhaft
        # stimmen. Statt zu raten wird der Nachbarport geprobt und der
        # gefundene uebernommen.
        if not self._port_gesucht:
            self._port_gesucht = True
            try:
                if await self._port_neu_suchen():
                    return await self._request(function, data)
            finally:
                self._port_gesucht = False

        raise SatisfactoryAPIError(f"API request failed after 3 attempts: {last_error}")

    async def _port_neu_suchen(self) -> bool:
        """Nachbarports auf die EIGENE antwortende API absuchen.

        Rueckgabe True, wenn ein Port uebernommen wurde; `self.port` und
        `self.base_url` zeigen dann dorthin.

        Zwei Regeln, ohne die die Suche schadet statt hilft:

        1. **Fremde Ports sind tabu.** Auf einer Maschine mit zwei Instanzen
           liegt der Nachbarport oft die andere Instanz — genau das passierte
           am 19./20.08.2026.
        2. **Antworten heisst nicht dazugehoeren.** `HealthCheck` braucht keine
           Anmeldung und antwortet deshalb auch dem falschen Server bereitwillig.
           Erst ein Aufruf MIT Token beweist, dass die API zu uns gehoert.

        Zuerst wird der konfigurierte Port geprobt: nach einem Neustart bindet
        der Server oft wieder dort, und dann gehoert der Client nach Hause.
        """
        kandidaten: list[int] = []
        for port in (self._port_konfiguriert, self.port + 1, self.port + 2,
                     self.port - 1):
            if port <= 0 or port > 65535 or port == self.port:
                continue
            if port in self.fremde_ports:
                logger.debug("Port %s gehoert einer anderen Instanz — uebersprungen", port)
                continue
            if port not in kandidaten:
                kandidaten.append(port)

        for port in kandidaten:
            url = f"https://{self.host}:{port}/api/v1"
            if not await self._gehoert_uns(url):
                continue

            heim = port == self._port_konfiguriert
            logger.warning(
                "Satisfactory-API antwortet auf Port %s statt %s — uebernommen%s.",
                port, self.port,
                " (zurueck auf dem konfigurierten Port)" if heim else "")
            self.port = port
            self.base_url = url
            return True

        return False

    async def _gehoert_uns(self, url: str) -> bool:
        """Prueft, ob unter `url` UNSERE API antwortet.

        Ohne Token bleibt nur `HealthCheck` — dann wird die Antwort als
        Zugehoerigkeit gewertet, weil es keine bessere Auskunft gibt. Mit Token
        entscheidet ein angemeldeter Aufruf: eine fremde Instanz weist unseren
        Token zurueck (HTTP 401), die eigene nicht.
        """
        session = await self._get_session()
        try:
            async with session.post(
                url,
                json={"function": "HealthCheck",
                      "data": {"clientCustomData": ""}},
                headers={"Content-Type": "application/json"},
                ssl=self._ssl,
            ) as resp:
                if resp.status != 200:
                    return False
                inhalt = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return False

        if not isinstance(inhalt, dict) or "data" not in inhalt:
            return False

        if not self.token:
            return True

        try:
            async with session.post(
                url,
                json={"function": "QueryServerState"},
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.token}"},
                ssl=self._ssl,
            ) as resp:
                if resp.status != 200:
                    logger.info(
                        "Port %s antwortet, weist unseren Token aber zurueck "
                        "(HTTP %s) — fremde Instanz, nicht uebernommen",
                        url.rsplit(":", 1)[-1].split("/")[0], resp.status)
                    return False
                antwort = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return False

        # Der Server antwortet auch auf abgelehnte Aufrufe mit HTTP 200 und
        # legt den Fehler in den Rumpf — deshalb reicht der Statuscode nicht.
        if isinstance(antwort, dict) and antwort.get("errorCode"):
            logger.info("Port antwortet mit %s — fremde Instanz, nicht uebernommen",
                        antwort.get("errorCode"))
            return False
        return True

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
            return ServerState(ok=False)

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
