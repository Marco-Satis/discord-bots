"""
F27: Gameserver Health-Check mit Auto-Restart
Background-Task fuer den Monitor Bot.

Prueft alle Server (Satisfactory + Minecraft) periodisch auf Erreichbarkeit
und fuehrt bei 3 aufeinanderfolgenden Fehlschlaegen einen automatischen
Restart per systemctl durch. Cooldown: max 1 Restart pro 30 Min pro Server.
"""

import asyncio
import socket
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import discord
from discord.ext import commands

from utils.logger import get_logger
from utils.config import get_config, get_env

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Embed-Farben
# ---------------------------------------------------------------------------
COLOR_WARNING: int = 0xF39C12
COLOR_ERROR: int = 0xE74C3C
COLOR_SUCCESS: int = 0x2ECC71

# ---------------------------------------------------------------------------
# Standard-Konfiguration (Fallbacks)
# ---------------------------------------------------------------------------
DEFAULT_CHECK_INTERVAL: int = 150          # Sekunden (~2.5 Min)
DEFAULT_FAILURE_THRESHOLD: int = 3         # Aufeinanderfolgende Fehlschlaege
DEFAULT_RESTART_COOLDOWN: int = 1800       # 30 Minuten in Sekunden
DEFAULT_CHECK_TIMEOUT: int = 10            # Sekunden pro Probe
# Satisfactory Game-Port (UDP) — Port 15777 (ServerQueryPort) ist bei vielen
# Setups nicht aktiv/gebunden. Stattdessen direkt den Game-Port 7777 per UDP
# pruefen, der nachweislich offen ist.
SAT_QUERY_PORT: int = 7777
MC_DEFAULT_PORTS: Dict[str, int] = {       # Minecraft Game-Ports pro Server-ID
    "BMC": 25566,
    "VANILLA": 25565,
}


# ---------------------------------------------------------------------------
# Datenklassen
# ---------------------------------------------------------------------------

@dataclass
class ServerProbe:
    """Ergebnis einer einzelnen Server-Pruefung."""
    server_id: str
    server_type: str                        # "sat" oder "mc"
    display_name: str
    service_name: str
    reachable: bool
    check_time: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None


@dataclass
class ServerState:
    """Laufender Zustand eines ueberwachten Servers."""
    server_id: str
    server_type: str
    display_name: str
    service_name: str
    consecutive_failures: int = 0
    last_restart: Optional[datetime] = None
    last_check: Optional[datetime] = None
    last_reachable: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Hauptklasse
# ---------------------------------------------------------------------------

class HealthAutoRestart:
    """
    Periodischer Health-Check fuer alle Gameserver mit automatischem Restart.

    Verwendung aus dem Monitor-Bot Background-Task::

        checker = HealthAutoRestart(bot)
        await checker.check_all_servers()
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot: commands.Bot = bot
        self._config: Dict[str, Any] = get_config()
        self._scheduler_cfg: Dict[str, Any] = self._config.get("scheduler", {})

        # Feature-Flags und Schwellwerte aus config.json/scheduler lesen
        self._enabled: bool = self._scheduler_cfg.get(
            "health_auto_restart_enabled", True
        )
        self._failure_threshold: int = self._scheduler_cfg.get(
            "health_auto_restart_failures", DEFAULT_FAILURE_THRESHOLD
        )
        self._restart_cooldown: int = self._scheduler_cfg.get(
            "health_auto_restart_cooldown", DEFAULT_RESTART_COOLDOWN
        )
        self._check_timeout: int = self._scheduler_cfg.get(
            "health_auto_restart_timeout", DEFAULT_CHECK_TIMEOUT
        )

        # Admin-Channel-ID aus ENV
        self._admin_channel_id: int = get_env(
            "ADMIN_LOG_CHANNEL_ID", cast=int, default=0
        )

        # Server-Zustaende: key = "<type>:<server_id>"  (z.B. "sat:main", "mc:BMC")
        self._states: Dict[str, ServerState] = {}

        # Unterdrueckte Server: key → Zeitpunkt bis wann unterdrueckt
        # Wird gesetzt wenn ein Server absichtlich gestoppt/neugestartet wird
        self._suppressed: Dict[str, datetime] = {}

        logger.info(
            "HealthAutoRestart initialisiert "
            f"(enabled={self._enabled}, threshold={self._failure_threshold}, "
            f"cooldown={self._restart_cooldown}s)"
        )

    # ------------------------------------------------------------------
    # Oeffentliche API
    # ------------------------------------------------------------------

    async def check_all_servers(self) -> List[ServerProbe]:
        """
        Fuehrt Health-Checks fuer alle konfigurierten Server durch und
        startet bei Bedarf automatische Restarts. Gibt die Probe-Ergebnisse
        zurueck.
        """
        if not self._enabled:
            logger.debug("HealthAutoRestart ist deaktiviert, ueberspringe Checks")
            return []

        probes: List[ServerProbe] = []

        # --- Satisfactory ---
        sat_probe = await self._check_satisfactory()
        if sat_probe is not None:
            probes.append(sat_probe)
            await self._process_probe(sat_probe)

        # --- Minecraft Server ---
        mc_probes = await self._check_all_minecraft()
        for probe in mc_probes:
            probes.append(probe)
            await self._process_probe(probe)

        return probes

    # ------------------------------------------------------------------
    # Satisfactory Health-Probe (UDP/TCP Query auf Port 15777)
    # ------------------------------------------------------------------

    async def _check_satisfactory(self) -> Optional[ServerProbe]:
        """Prueft den Satisfactory Server per Lightweight-Query auf Port 15777."""
        # Satisfactory Server-Objekt aus dem Bot holen (falls vorhanden)
        sat_server: Any = getattr(self.bot, "sat_server", None)
        if sat_server is None:
            # Kein Satisfactory-Server konfiguriert
            return None

        service_name: str = getattr(sat_server, "service_name", "satisfactory.service")
        display_name: str = "Satisfactory"
        server_id: str = "main"
        # SERVER_IP verwenden, nicht API_HOST — SAT bindet sich per -multihome
        # an die externe IP, nicht an localhost
        host: str = get_env("SERVER_IP", "127.0.0.1")
        port: int = SAT_QUERY_PORT

        # Leichtgewichtiger UDP/TCP Check
        reachable: bool = False
        error_msg: Optional[str] = None
        try:
            reachable = await self._probe_sat_query(host, port)
        except Exception as exc:
            error_msg = str(exc)
            logger.debug(f"SAT Query-Probe fehlgeschlagen: {exc}")

        return ServerProbe(
            server_id=server_id,
            server_type="sat",
            display_name=display_name,
            service_name=service_name,
            reachable=reachable,
            error=error_msg,
        )

    async def _probe_sat_query(self, host: str, port: int) -> bool:
        """
        Leichtgewichtiger Connectivity-Check fuer den Satisfactory Dedicated
        Server. Versucht zuerst UDP (Protocol Query), dann TCP-Fallback.
        Timeout gemaess Konfiguration (Standard 10s).
        """
        loop = asyncio.get_running_loop()

        # Versuch 1: UDP-Probe (Satisfactory Lightweight Query Protocol)
        try:
            result: bool = await asyncio.wait_for(
                loop.run_in_executor(None, self._udp_probe, host, port),
                timeout=self._check_timeout,
            )
            if result:
                return True
        except asyncio.TimeoutError:
            logger.debug(f"SAT UDP-Probe timeout ({host}:{port})")
        except OSError as exc:
            logger.debug(f"SAT UDP-Probe Fehler: {exc}")

        # Versuch 2: TCP-Fallback (einfacher Connect-Test)
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._tcp_probe, host, port),
                timeout=self._check_timeout,
            )
            return result
        except asyncio.TimeoutError:
            logger.debug(f"SAT TCP-Probe timeout ({host}:{port})")
        except OSError as exc:
            logger.debug(f"SAT TCP-Probe Fehler: {exc}")

        return False

    @staticmethod
    def _udp_probe(host: str, port: int) -> bool:
        """
        Sendet ein minimales UDP-Paket an den Satisfactory Query-Port
        und wartet auf eine Antwort. Gibt True zurueck falls eine
        Antwort kommt (beliebige Daten = Server lebt).
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(5)
            # Satisfactory Lightweight Query: Magic-Bytes senden
            # Protokoll-Header: 2 Bytes Protocol-ID + 1 Byte Packet-Type
            query_packet: bytes = struct.pack("<HB", 0xF6D5, 0x00)
            sock.sendto(query_packet, (host, port))
            data, _ = sock.recvfrom(1024)
            return len(data) > 0
        except (socket.timeout, OSError):
            return False
        finally:
            sock.close()

    @staticmethod
    def _tcp_probe(host: str, port: int) -> bool:
        """Einfacher TCP-Connect-Test als Fallback."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(5)
            sock.connect((host, port))
            return True
        except (socket.timeout, OSError, ConnectionRefusedError):
            return False
        finally:
            sock.close()

    # ------------------------------------------------------------------
    # Minecraft Health-Probes (TCP auf Game-Port)
    # ------------------------------------------------------------------

    async def _check_all_minecraft(self) -> List[ServerProbe]:
        """Prueft alle konfigurierten Minecraft-Server per TCP-Connect."""
        mc_servers: Dict[str, Any] = getattr(self.bot, "mc_servers", {})
        probes: List[ServerProbe] = []

        for sid, srv in mc_servers.items():
            probe = await self._check_minecraft(sid, srv)
            probes.append(probe)

        return probes

    async def _check_minecraft(self, server_id: str, srv: Any) -> ServerProbe:
        """
        Prueft einen einzelnen Minecraft-Server per TCP-Connect auf
        den Game-Port (25565/25566). Liest den Port aus ENV oder
        verwendet den Standard.
        """
        service_name: str = getattr(srv, "service_name", "")
        display_name: str = getattr(srv, "display_name", f"MC {server_id}")
        host: str = getattr(srv, "rcon_host", "127.0.0.1")

        # Game-Port aus ENV lesen (MC_{ID}_GAME_PORT) oder Default verwenden
        port: int = get_env(
            f"MC_{server_id.upper()}_GAME_PORT",
            default=MC_DEFAULT_PORTS.get(server_id.upper(), 25565),
            cast=int,
        )

        reachable: bool = False
        error_msg: Optional[str] = None

        try:
            loop = asyncio.get_running_loop()
            reachable = await asyncio.wait_for(
                loop.run_in_executor(None, self._tcp_probe, host, port),
                timeout=self._check_timeout,
            )
        except asyncio.TimeoutError:
            error_msg = f"TCP-Connect timeout ({host}:{port})"
            logger.debug(f"[{server_id}] {error_msg}")
        except OSError as exc:
            error_msg = str(exc)
            logger.debug(f"[{server_id}] MC TCP-Probe Fehler: {exc}")

        return ServerProbe(
            server_id=server_id,
            server_type="mc",
            display_name=display_name,
            service_name=service_name,
            reachable=reachable,
            error=error_msg,
        )

    # ------------------------------------------------------------------
    # Probe-Auswertung und Auto-Restart-Logik
    # ------------------------------------------------------------------

    def _get_state(self, probe: ServerProbe) -> ServerState:
        """Holt oder erstellt den laufenden Zustand fuer einen Server."""
        key: str = f"{probe.server_type}:{probe.server_id}"
        if key not in self._states:
            self._states[key] = ServerState(
                server_id=probe.server_id,
                server_type=probe.server_type,
                display_name=probe.display_name,
                service_name=probe.service_name,
            )
        return self._states[key]

    async def _process_probe(self, probe: ServerProbe) -> None:
        """
        Verarbeitet ein Probe-Ergebnis: zaehlt Fehlschlaege, loest
        ggf. Auto-Restart aus und sendet Discord-Benachrichtigungen.
        Unterdrueckte Server (absichtlicher Stop/Restart) werden uebersprungen.
        """
        # Pruefen ob Server absichtlich gestoppt wurde (kurzlebige In-Memory-Suppression)
        if self.is_suppressed(probe.server_type, probe.server_id):
            logger.debug(
                f"[{probe.display_name}] Health-Check unterdrueckt "
                f"(absichtlicher Stop/Restart)"
            )
            return

        # Pruefen ob Server dauerhaft manuell gestoppt wurde (persistenter State).
        # Dann: keine Failure-Warnings, keine Down-Notifications, kein Auto-Restart —
        # der Server SOLL offline sein.
        #
        # F01-Fix: Self-Healing bei Out-of-Band-Start. Wenn der Server als manuell
        # gestoppt markiert ist, aber tatsaechlich wieder erreichbar (z.B. via SSH/
        # Webmin statt Dashboard gestartet), wird der Flag automatisch geloescht und
        # die normale Health-Ueberwachung wieder aktiviert — sonst bliebe ein laufender
        # Server dauerhaft un-ueberwacht (Monitoring-Blindspot).
        try:
            from modules.monitoring import manual_stop_state
            if manual_stop_state.is_service_manually_stopped(probe.service_name):
                if probe.reachable:
                    server_id = manual_stop_state.SERVICE_TO_SERVER_ID.get(probe.service_name)
                    if server_id:
                        await manual_stop_state.mark_started(server_id)
                    logger.info(
                        f"[{probe.display_name}] wieder erreichbar trotz Manual-Stop-Flag "
                        f"(Out-of-Band-Start) — Flag geloescht, Health-Ueberwachung reaktiviert"
                    )
                    # weiter in den Normal-Pfad (Recovery-Handling unten)
                else:
                    state_ms: ServerState = self._get_state(probe)
                    state_ms.last_check = probe.check_time
                    state_ms.consecutive_failures = 0  # reset damit kein Restart bei spaeterem Start
                    logger.debug(
                        f"[{probe.display_name}] Health-Check unterdrueckt — "
                        f"manuell gestoppt (Service: {probe.service_name})"
                    )
                    return
        except Exception as e:
            logger.debug(f"manual_stop_state-Check fehlgeschlagen fuer '{probe.service_name}': {e}")

        state: ServerState = self._get_state(probe)
        state.last_check = probe.check_time

        if probe.reachable:
            # Server erreichbar — Recovery pruefen
            if state.consecutive_failures >= self._failure_threshold:
                # War vorher als ausgefallen markiert → Recovery-Meldung
                logger.info(
                    f"[{probe.display_name}] Server ist wieder erreichbar "
                    f"(nach {state.consecutive_failures} Fehlschlaegen)"
                )
                await self._notify_recovery(state)

            state.consecutive_failures = 0
            state.last_reachable = probe.check_time
            return

        # Server NICHT erreichbar
        state.consecutive_failures += 1
        logger.warning(
            f"[{probe.display_name}] Health-Check fehlgeschlagen "
            f"({state.consecutive_failures}/{self._failure_threshold}) "
            f"- {probe.error or 'nicht erreichbar'}"
        )

        # Warnung bei erstem Fehlschlag nach vorheriger Erreichbarkeit
        if state.consecutive_failures == 1:
            await self._notify_warning(state, probe)

        # Auto-Restart bei Schwellwert erreicht
        if state.consecutive_failures >= self._failure_threshold:
            await self._attempt_restart(state, probe)

    # ------------------------------------------------------------------
    # Auto-Restart per systemctl
    # ------------------------------------------------------------------

    async def _attempt_restart(self, state: ServerState, probe: ServerProbe) -> None:
        """
        Versucht einen automatischen Restart per systemctl. Beachtet den
        Cooldown von 30 Minuten pro Server.

        Respektiert manual_stop_state: wenn User Server bewusst gestoppt hat,
        kein Auto-Restart (consecutive_failures wird auch zurueckgesetzt damit
        nicht beim spaeteren Manual-Start eine alte Failure-Sequenz triggert).
        """
        now: datetime = datetime.now()

        # User-Override: Server manuell gestoppt → kein Auto-Restart
        try:
            from modules.monitoring import manual_stop_state
            if manual_stop_state.is_service_manually_stopped(state.service_name):
                logger.info(
                    f"[{state.display_name}] Auto-Restart uebersprungen — "
                    f"manuell gestoppt (Service: {state.service_name})"
                )
                # consecutive_failures reset damit nach manuellem Start nicht
                # sofort ein Restart triggert wegen alter Failures
                state.consecutive_failures = 0
                return
        except Exception as e:
            logger.debug(f"manual_stop_state-Check fehlgeschlagen fuer '{state.service_name}': {e}")

        # Cooldown pruefen
        if state.last_restart is not None:
            elapsed: float = (now - state.last_restart).total_seconds()
            if elapsed < self._restart_cooldown:
                remaining: int = int(self._restart_cooldown - elapsed)
                logger.warning(
                    f"[{state.display_name}] Restart-Cooldown aktiv, "
                    f"noch {remaining}s bis zum naechsten Restart moeglich"
                )
                # Nur bei genau dem Schwellwert benachrichtigen (nicht bei jedem weiteren Check)
                if state.consecutive_failures == self._failure_threshold:
                    await self._notify_cooldown(state, remaining)
                return

        # Restart ausfuehren
        logger.info(
            f"[{state.display_name}] Starte Auto-Restart "
            f"(nach {state.consecutive_failures} Fehlschlaegen)..."
        )
        await self._notify_restart_attempt(state)

        success: bool = False
        error_msg: str = ""

        try:
            returncode, stdout, stderr = await self._systemctl_restart(
                state.service_name
            )
            if returncode == 0:
                success = True
                state.last_restart = now
                state.consecutive_failures = 0
                logger.info(
                    f"[{state.display_name}] Auto-Restart erfolgreich "
                    f"(Service: {state.service_name})"
                )
            else:
                error_msg = stderr or stdout or f"Exit-Code: {returncode}"
                logger.error(
                    f"[{state.display_name}] Auto-Restart fehlgeschlagen: {error_msg}"
                )
        except asyncio.TimeoutError:
            error_msg = "systemctl restart Timeout (60s)"
            logger.error(f"[{state.display_name}] {error_msg}")
        except OSError as exc:
            error_msg = str(exc)
            logger.error(
                f"[{state.display_name}] Auto-Restart OS-Fehler: {exc}"
            )

        # Ergebnis-Benachrichtigung
        if success:
            await self._notify_restart_success(state)
        else:
            await self._notify_restart_failed(state, error_msg)

    async def _systemctl_restart(
        self, service_name: str, timeout: int = 60
    ) -> tuple[int, str, str]:
        """
        Fuehrt 'sudo systemctl restart <service>' asynchron aus.
        Gibt (returncode, stdout, stderr) zurueck.
        """
        proc = await asyncio.create_subprocess_exec(
            "sudo", "/usr/bin/systemctl", "restart", service_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return (
            proc.returncode or 0,
            stdout_bytes.decode("utf-8", errors="replace").strip(),
            stderr_bytes.decode("utf-8", errors="replace").strip(),
        )

    # ------------------------------------------------------------------
    # Discord-Benachrichtigungen (Admin-Channel Embeds)
    # ------------------------------------------------------------------

    def _get_admin_channel(self) -> Optional[discord.TextChannel]:
        """Holt den Admin-Log-Channel aus dem Bot-Cache."""
        if not self._admin_channel_id:
            return None
        channel = self.bot.get_channel(self._admin_channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    async def _send_embed(self, embed: discord.Embed) -> None:
        """Sendet ein Embed in den Admin-Channel (falls konfiguriert)."""
        channel: Optional[discord.TextChannel] = self._get_admin_channel()
        if channel is None:
            logger.debug("Kein Admin-Channel konfiguriert, Embed wird nicht gesendet")
            return
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            logger.error(f"Fehler beim Senden des Admin-Embeds: {exc}")

    async def _notify_warning(self, state: ServerState, probe: ServerProbe) -> None:
        """Sendet eine Warnung beim ersten Fehlschlag."""
        embed = discord.Embed(
            title=f"Health-Check Warnung: {state.display_name}",
            description=(
                f"Server **{state.display_name}** hat den Health-Check "
                f"nicht bestanden.\n\n"
                f"**Service:** `{state.service_name}`\n"
                f"**Fehler:** {probe.error or 'Nicht erreichbar'}\n"
                f"**Fehlschlaege:** {state.consecutive_failures}/"
                f"{self._failure_threshold}\n\n"
                f"Auto-Restart nach {self._failure_threshold} "
                f"aufeinanderfolgenden Fehlschlaegen."
            ),
            color=COLOR_WARNING,
            timestamp=datetime.now(),
        )
        embed.set_footer(text="Health Auto-Restart")
        await self._send_embed(embed)

    async def _notify_restart_attempt(self, state: ServerState) -> None:
        """Benachrichtigung: Auto-Restart wird gestartet."""
        embed = discord.Embed(
            title=f"Auto-Restart: {state.display_name}",
            description=(
                f"Server **{state.display_name}** war "
                f"**{state.consecutive_failures}x** nicht erreichbar.\n\n"
                f"**Service:** `{state.service_name}`\n"
                f"Fuehre `systemctl restart` aus..."
            ),
            color=COLOR_WARNING,
            timestamp=datetime.now(),
        )
        embed.set_footer(text="Health Auto-Restart")
        await self._send_embed(embed)

    async def _notify_restart_success(self, state: ServerState) -> None:
        """Benachrichtigung: Auto-Restart war erfolgreich."""
        embed = discord.Embed(
            title=f"Auto-Restart erfolgreich: {state.display_name}",
            description=(
                f"Server **{state.display_name}** wurde erfolgreich "
                f"neu gestartet.\n\n"
                f"**Service:** `{state.service_name}`\n"
                f"Der naechste Health-Check prueft die Erreichbarkeit."
            ),
            color=COLOR_SUCCESS,
            timestamp=datetime.now(),
        )
        embed.set_footer(text="Health Auto-Restart")
        await self._send_embed(embed)

    async def _notify_restart_failed(
        self, state: ServerState, error: str
    ) -> None:
        """Benachrichtigung: Auto-Restart ist fehlgeschlagen."""
        embed = discord.Embed(
            title=f"Auto-Restart FEHLGESCHLAGEN: {state.display_name}",
            description=(
                f"Server **{state.display_name}** konnte NICHT "
                f"automatisch neu gestartet werden!\n\n"
                f"**Service:** `{state.service_name}`\n"
                f"**Fehler:** {error}\n\n"
                f"Manuelles Eingreifen erforderlich."
            ),
            color=COLOR_ERROR,
            timestamp=datetime.now(),
        )
        embed.set_footer(text="Health Auto-Restart")
        await self._send_embed(embed)

    async def _notify_cooldown(self, state: ServerState, remaining: int) -> None:
        """Benachrichtigung: Restart-Cooldown ist aktiv."""
        minutes: int = remaining // 60
        seconds: int = remaining % 60
        embed = discord.Embed(
            title=f"Restart-Cooldown: {state.display_name}",
            description=(
                f"Server **{state.display_name}** ist weiterhin nicht "
                f"erreichbar, aber der Restart-Cooldown ist noch aktiv.\n\n"
                f"**Service:** `{state.service_name}`\n"
                f"**Verbleibend:** {minutes}m {seconds}s\n"
                f"**Fehlschlaege:** {state.consecutive_failures}\n\n"
                f"Max. 1 automatischer Restart pro "
                f"{self._restart_cooldown // 60} Minuten."
            ),
            color=COLOR_WARNING,
            timestamp=datetime.now(),
        )
        embed.set_footer(text="Health Auto-Restart")
        await self._send_embed(embed)

    async def _notify_recovery(self, state: ServerState) -> None:
        """Benachrichtigung: Server ist wieder erreichbar."""
        embed = discord.Embed(
            title=f"Recovery: {state.display_name}",
            description=(
                f"Server **{state.display_name}** ist wieder "
                f"erreichbar.\n\n"
                f"**Service:** `{state.service_name}`\n"
                f"War zuvor {state.consecutive_failures}x nicht "
                f"erreichbar."
            ),
            color=COLOR_SUCCESS,
            timestamp=datetime.now(),
        )
        embed.set_footer(text="Health Auto-Restart")
        await self._send_embed(embed)

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Gibt den aktuellen Zustand aller ueberwachten Server zurueck."""
        result: Dict[str, Any] = {}
        for key, state in self._states.items():
            suppressed_until = self._suppressed.get(key)
            result[key] = {
                "server_id": state.server_id,
                "server_type": state.server_type,
                "display_name": state.display_name,
                "service_name": state.service_name,
                "consecutive_failures": state.consecutive_failures,
                "last_restart": (
                    state.last_restart.isoformat() if state.last_restart else None
                ),
                "last_check": (
                    state.last_check.isoformat() if state.last_check else None
                ),
                "last_reachable": (
                    state.last_reachable.isoformat()
                    if state.last_reachable
                    else None
                ),
                "suppressed_until": (
                    suppressed_until.isoformat()
                    if suppressed_until and datetime.now() < suppressed_until
                    else None
                ),
            }
        return result

    def reset_state(self, server_type: str, server_id: str) -> None:
        """Setzt den Zustand eines Servers zurueck (z.B. nach manuellem Restart)."""
        key: str = f"{server_type}:{server_id}"
        if key in self._states:
            self._states[key].consecutive_failures = 0
            logger.info(f"Zustand zurueckgesetzt: {key}")

    @property
    def enabled(self) -> bool:
        """Gibt zurueck ob der Health-Auto-Restart aktiv ist."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Aktiviert oder deaktiviert den Health-Auto-Restart."""
        self._enabled = value
        logger.info(f"HealthAutoRestart {'aktiviert' if value else 'deaktiviert'}")

    def suppress(
        self, server_type: str, server_id: str, duration_seconds: int = 600
    ) -> None:
        """
        Unterdrueckt Health-Checks fuer einen Server fuer die angegebene Dauer.
        Wird aufgerufen wenn ein Server absichtlich gestoppt oder neugestartet wird,
        damit der Auto-Restart nicht faelschlicherweise ausloest.

        Args:
            server_type: "sat" oder "mc"
            server_id: z.B. "main", "BMC", "VANILLA"
            duration_seconds: Wie lange unterdruecken (Standard: 10 Minuten)
        """
        key = f"{server_type}:{server_id}"
        until = datetime.now() + timedelta(seconds=duration_seconds)
        self._suppressed[key] = until
        # Auch Failure-Counter zuruecksetzen
        if key in self._states:
            self._states[key].consecutive_failures = 0
        logger.info(
            f"Health-Check fuer {key} unterdrueckt bis "
            f"{until.strftime('%H:%M:%S')} ({duration_seconds}s)"
        )

    def unsuppress(self, server_type: str, server_id: str) -> None:
        """Hebt die Unterdrueckung fuer einen Server auf."""
        key = f"{server_type}:{server_id}"
        self._suppressed.pop(key, None)
        logger.info(f"Health-Check Unterdrueckung aufgehoben: {key}")

    def is_suppressed(self, server_type: str, server_id: str) -> bool:
        """Prueft ob Health-Checks fuer einen Server unterdrueckt sind."""
        key = f"{server_type}:{server_id}"
        until = self._suppressed.get(key)
        if until is None:
            return False
        if datetime.now() >= until:
            # Abgelaufen — automatisch aufheben
            del self._suppressed[key]
            logger.info(f"Health-Check Unterdrueckung abgelaufen: {key}")
            return False
        return True

    def reload_config(self) -> None:
        """Laedt die Konfiguration aus config.json neu."""
        self._config = get_config()
        self._scheduler_cfg = self._config.get("scheduler", {})
        self._enabled = self._scheduler_cfg.get(
            "health_auto_restart_enabled", True
        )
        self._failure_threshold = self._scheduler_cfg.get(
            "health_auto_restart_failures", DEFAULT_FAILURE_THRESHOLD
        )
        self._restart_cooldown = self._scheduler_cfg.get(
            "health_auto_restart_cooldown", DEFAULT_RESTART_COOLDOWN
        )
        self._check_timeout = self._scheduler_cfg.get(
            "health_auto_restart_timeout", DEFAULT_CHECK_TIMEOUT
        )
        logger.info("HealthAutoRestart Konfiguration neu geladen")
