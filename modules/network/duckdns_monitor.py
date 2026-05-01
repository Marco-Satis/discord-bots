"""
DuckDNS Monitor Module (F51) - Überprüft DNS-Auflösung gegen tatsächliche Server-IP.
Täglicher Check: Vergleicht die IP, die die DuckDNS-Domain aufgelöst,
mit der tatsächlichen externen IP des Servers (via api.ipify.org).
Bei Abweichung wird eine kritische Warnung ausgelöst.
"""

import asyncio
import socket
from datetime import datetime
from typing import Optional, Callable, Awaitable, Dict, List, Any

from utils.logger import get_logger
from utils.config import get_config, get_env

logger = get_logger("duckdns_monitor")

# Embed-Farben
COLOR_WARNING: int = 0xF39C12
COLOR_ERROR: int = 0xE74C3C
COLOR_SUCCESS: int = 0x2ECC71
COLOR_INFO: int = 0x5865F2

# Standard-Endpunkt fuer externe IP-Abfrage
DEFAULT_IP_API: str = "https://api.ipify.org"

# Tägliches Intervall in Sekunden (24 Stunden)
DAILY_INTERVAL: int = 86400


class DuckDNSMonitor:
    """
    Überprüft ob die DuckDNS-Domain auf die korrekte Server-IP zeigt.
    Vergleicht DNS-Auflösung mit der tatsächlichen externen IP.

    Usage:
        monitor = DuckDNSMonitor(bot, domain="mein-server.duckdns.org")
        monitor.on_mismatch = my_mismatch_callback
        result = await monitor.check_dns()
    """

    def __init__(
        self,
        bot: Any,
        *,
        domain: Optional[str] = None,
        check_interval: int = DAILY_INTERVAL,
        ip_api_url: str = DEFAULT_IP_API,
    ) -> None:
        self.bot: Any = bot
        self.check_interval: int = check_interval
        self.ip_api_url: str = ip_api_url

        # Domain aus Config oder Parameter laden
        cfg: Dict[str, Any] = get_config().get("duckdns", {})
        self.domain: str = domain or cfg.get(
            "domain",
            get_env("DUCKDNS_DOMAIN", "")
        )

        # Letzter bekannter Status
        self._last_result: Optional[Dict[str, Any]] = None
        self._task: Optional[asyncio.Task[None]] = None

        # Callbacks
        self.on_mismatch: Optional[
            Callable[[Dict[str, Any]], Awaitable[None]]
        ] = None  # Kritische Warnung bei IP-Abweichung
        self.on_match: Optional[
            Callable[[Dict[str, Any]], Awaitable[None]]
        ] = None  # Optionaler Callback bei Uebereinstimmung
        self.on_error: Optional[
            Callable[[str], Awaitable[None]]
        ] = None  # Callback bei Fehlern

        if self.domain:
            logger.info(f"DuckDNSMonitor initialisiert fuer Domain: {self.domain}")
        else:
            logger.warning("DuckDNSMonitor: Keine Domain konfiguriert!")

    # ------------------------------------------------------------------
    # Background-Task Steuerung
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Startet den täglichen Background-Check-Task."""
        if not self.domain:
            logger.error("DuckDNSMonitor kann nicht starten: Keine Domain konfiguriert")
            return

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._loop(), name="duckdns_monitor_loop"
            )
            logger.info("DuckDNSMonitor Background-Task gestartet")

    def stop(self) -> None:
        """Stoppt den Background-Check-Task."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("DuckDNSMonitor Background-Task gestoppt")

    async def _loop(self) -> None:
        """Endlos-Schleife: Prüft täglich die DNS-Auflösung."""
        try:
            while True:
                try:
                    result: Dict[str, Any] = await self.check_dns()
                    await self._handle_result(result)
                except asyncio.CancelledError:
                    raise
                except OSError as e:
                    logger.error(f"DuckDNS-Prüfung fehlgeschlagen: {e}")
                    if self.on_error:
                        await self.on_error(f"OS-Fehler: {e}")
                except RuntimeError as e:
                    logger.error(f"DuckDNS Runtime-Fehler: {e}")
                    if self.on_error:
                        await self.on_error(f"Runtime-Fehler: {e}")

                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            logger.info("DuckDNSMonitor Loop beendet")

    # ------------------------------------------------------------------
    # Haupt-Pruefmethode
    # ------------------------------------------------------------------

    async def check_dns(self) -> Dict[str, Any]:
        """
        Prüft die DNS-Auflösung der konfigurierten Domain
        und vergleicht sie mit der tatsächlichen externen IP.

        Returns:
            dict mit Keys:
                - domain: Die geprüft Domain
                - resolved_ip: IP aus DNS-Auflösung (oder None bei Fehler)
                - actual_ip: Tatsächliche externe IP (oder None bei Fehler)
                - match: True wenn beide IPs übereinstimmen
                - checked_at: ISO-Zeitstempel
                - error: Optionaler Fehlertext
        """
        result: Dict[str, Any] = {
            "domain": self.domain,
            "resolved_ip": None,
            "actual_ip": None,
            "match": False,
            "checked_at": datetime.now().isoformat(),
            "error": None,
        }

        if not self.domain:
            result["error"] = "Keine Domain konfiguriert"
            return result

        # DNS-Auflösung der Domain
        resolved_ip: Optional[str] = await self._resolve_domain(self.domain)
        result["resolved_ip"] = resolved_ip

        # Tatsächliche externe IP abrufen
        actual_ip: Optional[str] = await self._get_external_ip()
        result["actual_ip"] = actual_ip

        # Vergleich
        if resolved_ip is None:
            result["error"] = f"DNS-Auflösung für '{self.domain}' fehlgeschlagen"
            logger.error(result["error"])
        elif actual_ip is None:
            result["error"] = "Externe IP konnte nicht ermittelt werden"
            logger.error(result["error"])
        elif resolved_ip == actual_ip:
            result["match"] = True
            logger.info(
                f"DNS-Check OK: {self.domain} -> {resolved_ip} "
                f"(externe IP: {actual_ip})"
            )
        else:
            result["match"] = False
            logger.warning(
                f"DNS-ABWEICHUNG: {self.domain} -> {resolved_ip}, "
                f"aber externe IP ist {actual_ip}!"
            )

        self._last_result = result
        return result

    # ------------------------------------------------------------------
    # DNS-Auflösung
    # ------------------------------------------------------------------

    async def _resolve_domain(self, domain: str) -> Optional[str]:
        """
        Löst eine Domain per socket.getaddrinfo auf.
        Gibt die erste IPv4-Adresse zurück oder None bei Fehler.
        """
        loop = asyncio.get_running_loop()
        try:
            # getaddrinfo im Executor ausführen (blockierender I/O)
            addr_info: List[Any] = await loop.run_in_executor(
                None,
                lambda: socket.getaddrinfo(
                    domain, None, socket.AF_INET, socket.SOCK_STREAM
                ),
            )
            if addr_info:
                # Format: (family, type, proto, canonname, (ip, port))
                ip: str = addr_info[0][4][0]
                logger.debug(f"DNS aufgelöst: {domain} -> {ip}")
                return ip
            return None
        except socket.gaierror as e:
            logger.error(f"DNS-Auflösung fehlgeschlagen für '{domain}': {e}")
            return None
        except OSError as e:
            logger.error(f"Netzwerkfehler bei DNS-Auflösung: {e}")
            return None

    # ------------------------------------------------------------------
    # Externe IP ermitteln
    # ------------------------------------------------------------------

    async def _get_external_ip(self) -> Optional[str]:
        """
        Ermittelt die externe IP-Adresse des Servers via httpx (api.ipify.org).
        Fällt bei fehlendem httpx auf urllib zurück.
        """
        # Versuch 1: httpx (async, bevorzugt)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(self.ip_api_url)
                if response.status_code == 200:
                    ip: str = response.text.strip()
                    logger.debug(f"Externe IP (httpx): {ip}")
                    return ip
                logger.warning(
                    f"IP-API Antwort-Code: {response.status_code}"
                )
        except ImportError:
            logger.debug("httpx nicht verfügbar, Fallback auf urllib")
        except httpx.HTTPError as e:
            logger.warning(f"httpx Fehler bei IP-Abfrage: {e}")

        # Versuch 2: urllib Fallback (synchron im Executor)
        loop = asyncio.get_running_loop()
        try:
            import urllib.request
            ip_str: str = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(  # nosec B310 - ip_api_url ist hardcoded https-Konstante
                    self.ip_api_url, timeout=15
                ).read().decode("utf-8").strip(),
            )
            logger.debug(f"Externe IP (urllib): {ip_str}")
            return ip_str
        except urllib.error.URLError as e:
            logger.error(f"urllib Fehler bei IP-Abfrage: {e}")
            return None
        except OSError as e:
            logger.error(f"Netzwerkfehler bei IP-Abfrage: {e}")
            return None

    # ------------------------------------------------------------------
    # Reaktion auf Ergebnisse
    # ------------------------------------------------------------------

    async def _handle_result(self, result: Dict[str, Any]) -> None:
        """Reagiert auf das Prüfungsergebnis: Callback bei Match/Mismatch."""
        if result.get("error"):
            # Fehler beim Prüfen - kein klares Match/Mismatch
            return

        if result["match"]:
            if self.on_match:
                try:
                    await self.on_match(result)
                except Exception as e:
                    logger.error(f"on_match Callback-Fehler: {e}")
        else:
            # Kritische Warnung bei Abweichung
            if self.on_mismatch:
                try:
                    await self.on_mismatch(result)
                except Exception as e:
                    logger.error(f"on_mismatch Callback-Fehler: {e}")

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def get_embed_color(self, match: bool) -> int:
        """Gibt die passende Embed-Farbe zurück."""
        return COLOR_SUCCESS if match else COLOR_ERROR

    async def auto_update(self, token: str, ip: str) -> bool:
        """
        Aktualisiert den DuckDNS-DNS-Eintrag auf die angegebene IP.

        Args:
            token: DuckDNS API-Token
            ip: Neue IP-Adresse

        Returns:
            True wenn das Update erfolgreich war
        """
        if not self.domain:
            logger.error("auto_update: Keine Domain konfiguriert")
            return False

        # Subdomain aus der Domain extrahieren (z.B. "marco-satisfactory" aus "marco-satisfactory.duckdns.org")
        subdomain = self.domain.split(".")[0] if "." in self.domain else self.domain
        url = f"https://www.duckdns.org/update?domains={subdomain}&token={token}&ip={ip}"

        # httpx bevorzugen, Fallback auf urllib
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url)
                success = response.text.strip() == "OK"
                if success:
                    logger.info(f"DuckDNS auto_update erfolgreich: {subdomain} -> {ip}")
                else:
                    logger.warning(f"DuckDNS auto_update fehlgeschlagen: {response.text.strip()}")
                return success
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"DuckDNS auto_update httpx-Fehler: {e}")

        # Fallback: urllib
        loop = asyncio.get_running_loop()
        try:
            import urllib.request
            result = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(url, timeout=15).read().decode("utf-8").strip(),  # nosec B310 - DuckDNS-Endpoint ist hardcoded https://www.duckdns.org/update
            )
            success = result == "OK"
            if success:
                logger.info(f"DuckDNS auto_update erfolgreich (urllib): {subdomain} -> {ip}")
            else:
                logger.warning(f"DuckDNS auto_update fehlgeschlagen (urllib): {result}")
            return success
        except Exception as e:
            logger.error(f"DuckDNS auto_update Fehler: {e}")
            return False

    @property
    def last_result(self) -> Optional[Dict[str, Any]]:
        """Gibt das letzte Prüfungsergebnis zurück."""
        return self._last_result

    def format_status(self, result: Dict[str, Any]) -> str:
        """Formatiert das DNS-Prüfungsergebnis als lesbaren String."""
        status_icon: str = "OK" if result["match"] else "ABWEICHUNG"
        lines: list[str] = [
            f"**DuckDNS-Check: {status_icon}**",
            f"Domain: {result['domain']}",
            f"DNS-IP: {result['resolved_ip'] or 'N/A'}",
            f"Server-IP: {result['actual_ip'] or 'N/A'}",
        ]

        if result.get("error"):
            lines.append(f"Fehler: {result['error']}")

        if not result["match"] and result["resolved_ip"] and result["actual_ip"]:
            lines.append("")
            lines.append(
                "Die Domain zeigt auf eine andere IP als der Server hat! "
                "DuckDNS-Update erforderlich."
            )

        return "\n".join(lines)
