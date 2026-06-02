"""
Health Check Module - Crash Detection & Auto-Restart
Monitors Satisfactory server health via systemd + API
"""

import asyncio
from enum import Enum
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, Dict, List, Any

from utils.logger import get_logger

logger = get_logger("health_check")


class ServerState(Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    STARTING = "starting"
    OFFLINE = "offline"
    CRASHED = "crashed"
    RESTARTING = "restarting"


@dataclass
class HealthStatus:
    """Current health status snapshot"""
    state: ServerState = ServerState.UNKNOWN
    process_running: bool = False
    api_reachable: bool = False
    players_online: int = 0
    player_limit: int = 0
    tick_rate: float = 0.0
    session_name: str = ""
    uptime: int = 0
    cpu_percent: float = 0.0
    memory_mb: int = 0
    pid: Optional[int] = None
    last_check: Optional[datetime] = None
    consecutive_failures: int = 0


@dataclass
class CrashEvent:
    """Record of a crash event"""
    timestamp: datetime
    crash_number: int
    auto_restart_success: Optional[bool] = None
    restart_timestamp: Optional[datetime] = None


class HealthChecker:
    """
    Monitors server health and handles crash detection + auto-restart.
    
    Usage:
        checker = HealthChecker(server, api)
        checker.on_crash = my_crash_callback
        checker.on_recovery = my_recovery_callback
        status = await checker.check()
    """

    def __init__(self, server: Any, api: Any, *,
                 auto_restart: bool = True,
                 restart_delay: int = 30,
                 max_api_failures: int = 3) -> None:
        self.server = server
        self.api = api
        self.auto_restart = auto_restart
        self.restart_delay = restart_delay
        self.max_api_failures = max_api_failures

        self.status: HealthStatus = HealthStatus()
        self.crash_history: List[CrashEvent] = []
        self._max_crash_history: int = 100
        self._api_fail_count: int = 0

        # Callbacks
        self.on_crash: Optional[Callable[[CrashEvent], Awaitable]] = None
        self.on_recovery: Optional[Callable[[], Awaitable]] = None
        self.on_restart_success: Optional[Callable[[CrashEvent], Awaitable]] = None
        self.on_restart_failed: Optional[Callable[[CrashEvent, str], Awaitable]] = None
        self.on_performance_warning: Optional[Callable[[List[str]], Awaitable]] = None
        # Optionaler Check: liefert True wenn Crash-Detection unterdrueckt werden
        # soll (z.B. geplanter Stop fuer Update/Restart -> kein False-Crash).
        self.suppress_crash_check: Optional[Callable[[], bool]] = None

    @property
    def crash_count(self) -> int:
        return len(self.crash_history)

    @property
    def is_online(self) -> bool:
        return self.status.state == ServerState.ONLINE

    @property
    def crashes_last_hour(self) -> int:
        cutoff = datetime.now() - timedelta(hours=1)
        return sum(1 for c in self.crash_history if c.timestamp > cutoff)

    async def check(self) -> HealthStatus:
        """Perform a full health check cycle"""
        prev_state = self.status.state

        # Check process
        running = await self.server.is_running()
        self.status.process_running = running

        if running:
            # Get process stats
            proc_status = await self.server.get_status()
            self.status.pid = proc_status.get("pid")
            self.status.cpu_percent = proc_status.get("cpu_percent", 0)
            self.status.memory_mb = proc_status.get("memory_mb", 0)
            self.status.uptime = proc_status.get("uptime", 0)

            # Check API
            try:
                health = await self.api.health_check()
                if health.health != "offline":
                    self.status.api_reachable = True
                    self._api_fail_count = 0

                    # Get detailed state
                    state = await self.api.query_server_state()
                    self.status.players_online = state.num_players
                    self.status.player_limit = state.player_limit
                    self.status.tick_rate = state.average_tick_rate
                    self.status.session_name = state.active_session or ""
                    self.status.state = ServerState.ONLINE
                    self.status.consecutive_failures = 0
                else:
                    self._api_fail_count += 1
                    self.status.api_reachable = False
                    # Process running but API says offline = starting up
                    self.status.state = ServerState.STARTING
            except Exception as e:
                self._api_fail_count += 1
                self.status.api_reachable = False
                logger.debug(f"API check failed ({self._api_fail_count}): {e}")

                if self._api_fail_count >= self.max_api_failures:
                    # Process running but API consistently fails
                    self.status.state = ServerState.STARTING
                else:
                    # Might still be starting up
                    if prev_state == ServerState.ONLINE:
                        self.status.state = ServerState.ONLINE  # Brief hiccup
                    else:
                        self.status.state = ServerState.STARTING
        else:
            self.status.api_reachable = False
            self.status.players_online = 0
            self._api_fail_count = 0

            # Detect crash: was online/starting, now process dead
            if prev_state in (ServerState.ONLINE, ServerState.STARTING):
                # Geplanter Stop (Update/Restart)? -> kein Crash, kein Alarm.
                suppressed = False
                if self.suppress_crash_check is not None:
                    try:
                        suppressed = bool(self.suppress_crash_check())
                    except Exception as e:
                        logger.debug(f"suppress_crash_check Fehler: {e}")
                if suppressed:
                    logger.info("Prozess gestoppt im unterdrueckten Fenster "
                                "(geplanter Update/Restart) — kein Crash")
                    self.status.state = ServerState.OFFLINE
                else:
                    self.status.state = ServerState.CRASHED
                    await self._handle_crash()
            else:
                self.status.state = ServerState.OFFLINE

        # Detect recovery: was offline/crashed, now online
        if prev_state in (ServerState.OFFLINE, ServerState.CRASHED) \
                and self.status.state == ServerState.ONLINE:
            logger.info("Server recovery detected")
            if self.on_recovery:
                try:
                    await self.on_recovery()
                except Exception as e:
                    logger.error(f"Recovery callback error: {e}")

        self.status.last_check = datetime.now()
        return self.status

    async def _handle_crash(self) -> None:
        """Handle a detected crash"""
        crash = CrashEvent(
            timestamp=datetime.now(),
            crash_number=self.crash_count + 1
        )
        self.crash_history.append(crash)
        if len(self.crash_history) > self._max_crash_history:
            self.crash_history = self.crash_history[-self._max_crash_history:]
        logger.warning(f"Crash #{crash.crash_number} detected!")

        # Notify
        if self.on_crash:
            try:
                await self.on_crash(crash)
            except Exception as e:
                logger.error(f"Crash callback error: {e}")

        # Auto-restart (unless too many crashes recently)
        if self.auto_restart:
            if self.crashes_last_hour > 5:
                logger.error("Too many crashes in last hour, skipping auto-restart")
                if self.on_restart_failed:
                    try:
                        await self.on_restart_failed(
                            crash, "Zu viele Crashes in der letzten Stunde (>5)"
                        )
                    except Exception as e:
                        logger.debug(f"on_restart_failed callback error: {e}")
                return

            logger.info(f"Auto-restart in {self.restart_delay}s...")
            self.status.state = ServerState.RESTARTING
            await asyncio.sleep(self.restart_delay)

            success, msg = await self.server.start()
            crash.auto_restart_success = success
            crash.restart_timestamp = datetime.now()

            if success:
                logger.info(f"Auto-restart successful: {msg}")
                if self.on_restart_success:
                    try:
                        await self.on_restart_success(crash)
                    except Exception as e:
                        logger.error(f"Restart success callback error: {e}")
            else:
                logger.error(f"Auto-restart failed: {msg}")
                self.status.state = ServerState.OFFLINE
                if self.on_restart_failed:
                    try:
                        await self.on_restart_failed(crash, msg)
                    except Exception as e:
                        logger.error(f"Restart failed callback error: {e}")

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary dict of current health status"""
        return {
            "state": self.status.state.value,
            "process_running": self.status.process_running,
            "api_reachable": self.status.api_reachable,
            "players": f"{self.status.players_online}/{self.status.player_limit}",
            "tick_rate": self.status.tick_rate,
            "uptime": self.status.uptime,
            "cpu": self.status.cpu_percent,
            "ram_mb": self.status.memory_mb,
            "crashes_total": self.crash_count,
            "crashes_last_hour": self.crashes_last_hour,
            "last_check": self.status.last_check.isoformat() if self.status.last_check else None,
        }
