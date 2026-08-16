"""
Port Monitor Module (F52) - Prueft die Erreichbarkeit konfigurierter TCP-Ports.
Alle 5 Minuten wird ein TCP-Connect-Test auf die konfigurierten Ports durchgefuehrt.
Ports werden nur geprueft wenn der zugehoerige Server/Service aktiv sein soll.
Bei geschlossenem Port wird eine Warnung ausgeloest.
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable, Dict, List, Any

from utils.logger import get_logger
from utils.config import get_config, get_env

logger = get_logger("port_monitor")

# Embed-Farben
COLOR_WARNING: int = 0xF39C12
COLOR_ERROR: int = 0xE74C3C
COLOR_SUCCESS: int = 0x2ECC71
COLOR_INFO: int = 0x5865F2

# Standard-Port-Konfiguration
# Hinweis: Nur TCP-Ports hier eintragen!
# SAT Port 7777 ist UDP-only (kein TCP) → wird vom Health-Checker per
# UDP-Probe geprueft, nicht hier im TCP-Port-Monitor.
DEFAULT_PORTS: list[Dict[str, Any]] = [
    {"port": 8080, "label": "Web-Dashboard", "host": "127.0.0.1", "enabled": True},
]

# Timeout fuer einzelne Port-Checks (Sekunden)
DEFAULT_CONNECT_TIMEOUT: float = 5.0

# Port → Kennung fuer die manual_stop_state-Unterdrueckung: manuell gestoppte
# Server sollen keine Port-Down-Warnungen erzeugen.
#
# Gebaut aus der ENV (modules.server_registry) statt aufgezaehlt — bis
# 2026-08-14 standen hier zwei Vanilla-Ports, die nach der Stilllegung auf einen
# Server gezeigt haetten, den es nicht mehr gibt.


def _port_zuordnung() -> dict[int, str]:
    """Ports aller konfigurierten Server auf ihre Kennung abbilden."""
    from modules.server_registry import alle as alle_server

    zuordnung: dict[int, str] = {}
    for srv in alle_server():
        if srv.spiel == "minecraft":
            kandidaten = [
                get_env(f"{srv.env_praefix}RCON_PORT", 25575, cast=int),
                get_env(f"{srv.env_praefix}GAME_PORT", 0, cast=int),
            ]
        else:
            kandidaten = [
                get_env(f"{srv.env_praefix}PORT", 7777, cast=int),
                get_env(f"{srv.env_praefix}QUERY_PORT", 15777, cast=int),
            ]
        for port in kandidaten:
            if port:
                zuordnung[port] = srv.kennung
    return zuordnung


PORT_TO_SERVER_ID: dict[int, str] = _port_zuordnung()


def _standard_ports() -> list[Dict[str, Any]]:
    """Zu ueberwachende Ports: Dashboard plus die Spiel-Ports aller Server.

    Anlass (Audit-Befund, 2026-08-16): Ohne `port_monitor.ports` in der
    config.json griff `DEFAULT_PORTS` — und das war **ein einziger Eintrag**,
    das Dashboard auf 127.0.0.1. Im Log stand jahrelang unauffaellig
    "PortMonitor initialisiert: 1/1 Ports aktiv".

    Damit ueberwachte ausgerechnet die Port-Ueberwachung keinen einzigen Port,
    auf den ein Spieler verbindet. Am selben Tag war Satisfactory sieben
    Minuten nicht erreichbar, weil sich zwei Instanzen einen Port teilten —
    genau der Fall, den diese Pruefung finden soll.

    Die Liste kommt aus der Registry statt aus einer Aufzaehlung: kommt ein
    Server dazu, wird er mitueberwacht, ohne dass jemand daran denken muss.
    Faellt einer weg, verschwindet er ebenso. Das ist dieselbe Regel, die im
    Rest des Projekts gilt.
    """
    ports: list[Dict[str, Any]] = [
        {"port": 8080, "label": "Web-Dashboard", "host": "127.0.0.1", "enabled": True},
    ]
    try:
        from modules.server_registry import alle as alle_server

        erster_sat = True
        for srv in alle_server():
            if srv.spiel == "minecraft":
                # Der Spielport steht bei Minecraft nicht zwingend in der ENV.
                # Dann wird der RCON-Port geprueft: er ist gesetzt, er gehoert
                # zum selben Prozess, und die Bots benutzen ihn ohnehin. Ein
                # erreichbarer RCON-Port heisst, der Server laeuft.
                port = get_env(f"{srv.env_praefix}GAME_PORT", 0, cast=int)
                zusatz = "Spiel"
                if not port:
                    port = get_env(f"{srv.env_praefix}RCON_PORT", 0, cast=int)
                    zusatz = "RCON"
            else:
                # Die erste Satisfactory-Instanz laeuft historisch ohne eigenen
                # SAT_<ID>_PORT-Eintrag auf der Vorgabe 7777 — dieselbe Annahme
                # trifft `_port_zuordnung()` weiter oben. Fuer jede weitere
                # Instanz waere Raten gefaehrlich: sie koennte auf einem
                # beliebigen Port liegen, und ein falsch ueberwachter Port
                # meldet entweder nie oder dauernd.
                port = get_env(f"{srv.env_praefix}PORT", 7777 if erster_sat else 0,
                               cast=int)
                zusatz = "Spiel"
                erster_sat = False
            if port:
                ports.append({
                    "port": port,
                    "label": f"{srv.label} ({zusatz})",
                    # Von aussen pruefen waere aussagekraeftiger, geht hier aber
                    # nicht zuverlaessig — localhost belegt immerhin, dass der
                    # Serverprozess lauscht.
                    "host": "127.0.0.1",
                    "enabled": True,
                })
            else:
                logger.warning(
                    f"Kein pruefbarer Port fuer {srv.label} gefunden "
                    f"({srv.env_praefix}PORT / GAME_PORT / RCON_PORT) — "
                    f"dieser Server wird NICHT ueberwacht."
                )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Spiel-Ports konnten nicht aus der Registry gelesen werden: {e}")
    return ports


def _port_belongs_to_stopped_server(port: int) -> bool:
    """True wenn der Port zu einem manuell gestoppten Server gehoert."""
    server_id = PORT_TO_SERVER_ID.get(port)
    if not server_id:
        return False
    try:
        from modules.monitoring import manual_stop_state
        return manual_stop_state.is_manually_stopped(server_id)
    except Exception:
        return False


class PortMonitor:
    """
    Ueberprueft die Erreichbarkeit von TCP-Ports auf konfigurierten Hosts.
    Nur Ports die als aktiv markiert sind werden geprueft.

    Usage:
        monitor = PortMonitor(bot)
        monitor.on_port_closed = my_closed_callback
        results = await monitor.check_ports()
    """

    def __init__(
        self,
        bot: Any,
        *,
        check_interval: int = 300,  # 5 Minuten
        ports: Optional[List[Dict[str, Any]]] = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        self.bot: Any = bot
        self.check_interval: int = check_interval
        self.connect_timeout: float = connect_timeout

        # Port-Liste aus Config oder Parameter laden
        cfg: Dict[str, Any] = get_config().get("port_monitor", {})
        if ports is not None:
            self.ports: List[Dict[str, Any]] = ports
        else:
            self.ports = cfg.get("ports") or _standard_ports()

        # Task-Referenz
        self._task: Optional[asyncio.Task[None]] = None

        # Letzter bekannter Status pro Port
        self._last_results: List[Dict[str, Any]] = []

        # Alert-Cooldown: Verhindert Spam bei dauerhaft geschlossenen Ports
        self._alert_cooldown: int = cfg.get("alert_cooldown_seconds", 600)  # 10 Min
        self._last_alert_time: Dict[int, datetime] = {}

        # Alert erst nach N aufeinanderfolgenden Fehlschlaegen.
        # Grund: geplante Restarts (MC Daily-Restart 05:00, Modpack-Update) machen den
        # Port fuer ~1-3 Minuten zu. Bei check_interval=300s trifft ein einzelner Check
        # dieses Fenster und erzeugte bisher taeglich einen Fehlalarm.
        self._alert_after_failures: int = max(1, int(cfg.get("alert_after_failures", 2)))
        self._consecutive_failures: Dict[int, int] = {}
        # Ports fuer die aktuell ein Alert offen ist (fuer Recovery-Meldung)
        self._alerted_ports: set[int] = set()

        # Callbacks
        self.on_port_closed: Optional[
            Callable[[Dict[str, Any]], Awaitable[None]]
        ] = None  # Einzelner Port ist geschlossen
        self.on_port_recovered: Optional[
            Callable[[Dict[str, Any]], Awaitable[None]]
        ] = None  # Port ist wieder offen
        self.on_check_complete: Optional[
            Callable[[List[Dict[str, Any]]], Awaitable[None]]
        ] = None  # Alle Checks fertig

        active_count: int = sum(1 for p in self.ports if p.get("enabled", True))
        logger.info(
            f"PortMonitor initialisiert: {active_count}/{len(self.ports)} Ports aktiv, "
            f"Intervall={check_interval}s, Timeout={connect_timeout}s"
        )

    # ------------------------------------------------------------------
    # Background-Task Steuerung
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Startet den Background-Monitoring-Task."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._loop(), name="port_monitor_loop"
            )
            logger.info("PortMonitor Background-Task gestartet")

    def stop(self) -> None:
        """Stoppt den Background-Monitoring-Task."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("PortMonitor Background-Task gestoppt")

    async def _loop(self) -> None:
        """Endlos-Schleife: Prueft alle check_interval Sekunden die Ports."""
        try:
            while True:
                try:
                    results: List[Dict[str, Any]] = await self.check_ports()
                    await self._handle_results(results)
                except asyncio.CancelledError:
                    raise
                except OSError as e:
                    logger.error(f"PortMonitor Pruefung fehlgeschlagen: {e}")
                except RuntimeError as e:
                    logger.error(f"PortMonitor Runtime-Fehler: {e}")

                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            logger.info("PortMonitor Loop beendet")

    # ------------------------------------------------------------------
    # Haupt-Pruefmethode
    # ------------------------------------------------------------------

    async def check_ports(self) -> List[Dict[str, Any]]:
        """
        Prueft alle konfigurierten und aktivierten Ports per TCP-Connect-Test.

        Returns:
            Liste von Dicts mit Keys:
                - port: Port-Nummer
                - label: Menschenlesbarer Name
                - open: True wenn der Port erreichbar ist
                - response_ms: Antwortzeit in Millisekunden (oder -1 bei Fehler)
                - host: Gepruefter Host
                - checked_at: ISO-Zeitstempel
                - error: Optionaler Fehlertext bei geschlossenem Port
        """
        results: List[Dict[str, Any]] = []

        # Nur aktive Ports pruefen (deren Server laufen soll)
        active_ports: List[Dict[str, Any]] = [
            p for p in self.ports if p.get("enabled", True)
        ]

        # Alle Port-Checks parallel ausfuehren
        tasks: list[asyncio.Task[Dict[str, Any]]] = [
            asyncio.create_task(self._check_single_port(port_cfg))
            for port_cfg in active_ports
        ]

        if tasks:
            completed: List[Dict[str, Any]] = await asyncio.gather(
                *tasks, return_exceptions=False
            )
            results.extend(completed)

        self._last_results = results
        return results

    async def _check_single_port(
        self, port_cfg: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Prueft einen einzelnen Port per TCP-Connect-Test.

        Returns:
            Dict mit Port-Status und Antwortzeit.
        """
        port: int = port_cfg["port"]
        label: str = port_cfg.get("label", f"Port {port}")
        host: str = port_cfg.get("host", "127.0.0.1")

        result: Dict[str, Any] = {
            "port": port,
            "label": label,
            "open": False,
            "response_ms": -1.0,
            "host": host,
            "checked_at": datetime.now().isoformat(),
            "error": None,
        }

        start_time: float = time.monotonic()

        try:
            # TCP-Connect-Test
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=self.connect_timeout,
            )
            elapsed_ms: float = (time.monotonic() - start_time) * 1000

            result["open"] = True
            result["response_ms"] = round(elapsed_ms, 2)

            # Verbindung sauber schliessen
            writer.close()
            await writer.wait_closed()

            logger.debug(
                f"Port {port} ({label}) auf {host}: offen ({elapsed_ms:.1f}ms)"
            )

        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            result["response_ms"] = round(elapsed_ms, 2)
            result["error"] = f"Timeout nach {self.connect_timeout}s"
            logger.debug(f"Port {port} ({label}) auf {host}: Timeout")

        except ConnectionRefusedError:
            result["error"] = "Verbindung abgelehnt"
            logger.debug(f"Port {port} ({label}) auf {host}: Verbindung abgelehnt")

        except OSError as e:
            result["error"] = f"Netzwerkfehler: {e}"
            logger.debug(f"Port {port} ({label}) auf {host}: {e}")

        return result

    # ------------------------------------------------------------------
    # Reaktion auf Ergebnisse
    # ------------------------------------------------------------------

    async def _handle_results(self, results: List[Dict[str, Any]]) -> None:
        """Verarbeitet die Pruefergebnisse und loest ggf. Callbacks aus."""
        for result in results:
            port: int = result["port"]
            is_open: bool = result["open"]

            if not is_open:
                # Manuell gestoppter Server → keine Warning (Server SOLL offline sein)
                if _port_belongs_to_stopped_server(port):
                    self._consecutive_failures[port] = 0
                    logger.debug(
                        f"Port {port} ({result['label']}) zu — Server manuell gestoppt, "
                        f"keine Warnung"
                    )
                    continue

                # Fehlschlaege zaehlen — kurze Ausfaelle (geplanter Restart) nicht melden
                fails: int = self._consecutive_failures.get(port, 0) + 1
                self._consecutive_failures[port] = fails
                if fails < self._alert_after_failures:
                    logger.debug(
                        f"Port {port} ({result['label']}) zu "
                        f"({fails}/{self._alert_after_failures}) — noch kein Alert "
                        f"(vermutlich geplanter Restart)"
                    )
                    continue

                # Port ist geschlossen
                if self._should_alert(port):
                    self._alerted_ports.add(port)
                    logger.warning(
                        f"Port {port} ({result['label']}) auf {result['host']} "
                        f"ist NICHT erreichbar: {result.get('error', 'unbekannt')}"
                    )
                    if self.on_port_closed:
                        try:
                            await self.on_port_closed(result)
                        except Exception as e:
                            logger.error(f"on_port_closed Callback-Fehler: {e}")

            else:
                # Port offen — Fehlschlag-Zaehler zuruecksetzen
                self._consecutive_failures[port] = 0

                # Recovery nur melden wenn vorher wirklich ein Alert rausging
                if port in self._alerted_ports:
                    self._alerted_ports.discard(port)
                    self._last_alert_time.pop(port, None)
                    logger.info(
                        f"Port {port} ({result['label']}) ist wieder erreichbar "
                        f"({result['response_ms']}ms)"
                    )
                    if self.on_port_recovered:
                        try:
                            await self.on_port_recovered(result)
                        except Exception as e:
                            logger.error(f"on_port_recovered Callback-Fehler: {e}")

        # Gesamter Check abgeschlossen
        if self.on_check_complete:
            try:
                await self.on_check_complete(results)
            except Exception as e:
                logger.error(f"on_check_complete Callback-Fehler: {e}")

    def _should_alert(self, port: int) -> bool:
        """Prueft ob ein Alert fuer diesen Port gesendet werden soll (Cooldown)."""
        now = datetime.now()
        last: Optional[datetime] = self._last_alert_time.get(port)

        if last is None or (now - last).total_seconds() > self._alert_cooldown:
            self._last_alert_time[port] = now
            return True
        return False

    # ------------------------------------------------------------------
    # Port-Verwaltung
    # ------------------------------------------------------------------

    def enable_port(self, port: int) -> bool:
        """Aktiviert die Ueberwachung fuer einen Port."""
        for p in self.ports:
            if p["port"] == port:
                p["enabled"] = True
                logger.info(f"Port {port} ({p.get('label', '')}) aktiviert")
                return True
        return False

    def disable_port(self, port: int) -> bool:
        """Deaktiviert die Ueberwachung fuer einen Port."""
        for p in self.ports:
            if p["port"] == port:
                p["enabled"] = False
                logger.info(f"Port {port} ({p.get('label', '')}) deaktiviert")
                return True
        return False

    def add_port(
        self,
        port: int,
        label: str,
        host: str = "127.0.0.1",
        enabled: bool = True,
    ) -> None:
        """Fuegt einen neuen Port zur Ueberwachung hinzu."""
        # Duplikate vermeiden
        for p in self.ports:
            if p["port"] == port and p.get("host", "127.0.0.1") == host:
                logger.warning(f"Port {port} auf {host} ist bereits konfiguriert")
                return

        self.ports.append({
            "port": port,
            "label": label,
            "host": host,
            "enabled": enabled,
        })
        logger.info(f"Port {port} ({label}) auf {host} hinzugefuegt")

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def get_embed_color(self, all_open: bool) -> int:
        """Gibt die passende Embed-Farbe zurueck."""
        return COLOR_SUCCESS if all_open else COLOR_WARNING

    @property
    def last_results(self) -> List[Dict[str, Any]]:
        """Gibt die letzten Pruefergebnisse zurueck."""
        return self._last_results

    @property
    def all_ports_open(self) -> bool:
        """Prueft ob alle ueberwachten Ports offen sind."""
        if not self._last_results:
            return False
        return all(r["open"] for r in self._last_results)

    def get_summary(self) -> Dict[str, Any]:
        """Gibt eine Zusammenfassung aller Port-Status zurueck."""
        open_count: int = sum(1 for r in self._last_results if r["open"])
        total: int = len(self._last_results)

        return {
            "total_ports": total,
            "open_ports": open_count,
            "closed_ports": total - open_count,
            "all_open": self.all_ports_open,
            "ports": self._last_results,
        }

    def format_status(self, results: Optional[List[Dict[str, Any]]] = None) -> str:
        """Formatiert die Port-Status als lesbaren String."""
        data: List[Dict[str, Any]] = results or self._last_results

        if not data:
            return "Keine Port-Daten verfuegbar."

        lines: list[str] = ["**Port-Status:**", ""]

        for result in data:
            if result["open"]:
                icon = "+"
                detail = f"{result['response_ms']}ms"
            else:
                icon = "-"
                detail = result.get("error", "geschlossen")

            lines.append(
                f"[{icon}] {result['label']} "
                f"({result['host']}:{result['port']}): {detail}"
            )

        open_count: int = sum(1 for r in data if r["open"])
        lines.append("")
        lines.append(f"Gesamt: {open_count}/{len(data)} Ports erreichbar")

        return "\n".join(lines)
