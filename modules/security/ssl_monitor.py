"""
F32: SSL Monitor — Ueberwacht Zertifikat-Ablaufdaten.

Prueft taeglichen SSL/TLS-Zertifikatstatus fuer konfigurierte Domains.
Warnt 14 Tage vor Ablauf, meldet kritisch 3 Tage vor Ablauf.
"""

import asyncio
import ssl
import socket
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger
from utils.config import get_config, get_env, PROJECT_ROOT

logger = get_logger("security.ssl_monitor")

# Schwellenwerte fuer Warnungen (in Tagen)
WARNING_DAYS: int = 14
CRITICAL_DAYS: int = 3

# Standard-Port fuer HTTPS
DEFAULT_PORT: int = 443

# Timeout fuer SSL-Verbindung (Sekunden)
SSL_TIMEOUT: float = 15.0


class SSLMonitor:
    """
    Ueberwacht SSL/TLS-Zertifikate auf ihre Gueltigkeit.

    Liest die Domain aus der Umgebungsvariable WEB_DOMAIN oder
    faellt auf den Standard "marco-satisfactory.duckdns.org" zurueck.
    """

    def __init__(
        self,
        domain: Optional[str] = None,
        port: int = DEFAULT_PORT,
        warning_days: int = WARNING_DAYS,
        critical_days: int = CRITICAL_DAYS,
    ) -> None:
        """
        Args:
            domain: Domain die geprueft werden soll (None = aus ENV lesen)
            port: HTTPS-Port (Standard: 443)
            warning_days: Tage vor Ablauf fuer Warnung (Standard: 14)
            critical_days: Tage vor Ablauf fuer kritische Warnung (Standard: 3)
        """
        self.domain: str = domain or get_env("WEB_DOMAIN", "marco-satisfactory.duckdns.org")
        self.port: int = port
        self.warning_days: int = warning_days
        self.critical_days: int = critical_days
        self._last_result: Optional[dict] = None

        logger.info(
            "SSLMonitor initialisiert fuer %s:%d (Warnung: %dT, Kritisch: %dT)",
            self.domain, self.port, self.warning_days, self.critical_days
        )

    def _fetch_certificate(self) -> dict:
        """
        Holt das SSL-Zertifikat einer Domain (blockierend).

        Wird via asyncio.to_thread aufgerufen.

        Returns:
            Dict mit Zertifikat-Informationen (aus ssl.getpeercert())

        Raises:
            ssl.SSLError: Bei SSL-Problemen
            socket.error: Bei Verbindungsproblemen
        """
        # SSL-Kontext erstellen der das Zertifikat prueft
        ctx = ssl.create_default_context()

        with socket.create_connection(
            (self.domain, self.port), timeout=SSL_TIMEOUT
        ) as sock:
            with ctx.wrap_socket(sock, server_hostname=self.domain) as ssl_sock:
                cert = ssl_sock.getpeercert()
                if cert is None:
                    raise ssl.SSLError("Kein Zertifikat vom Server erhalten")
                return cert

    @staticmethod
    def _parse_cert_date(date_str: str) -> datetime:
        """
        Parst ein Zertifikat-Datum im OpenSSL-Format.

        Args:
            date_str: Datum im Format "MMM DD HH:MM:SS YYYY GMT"

        Returns:
            datetime-Objekt (UTC)
        """
        # OpenSSL-Format: "Jan  5 09:30:00 2025 GMT"
        return datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )

    def _determine_status(self, days_remaining: int) -> str:
        """
        Bestimmt den Status basierend auf verbleibenden Tagen.

        Args:
            days_remaining: Verbleibende Tage bis Ablauf

        Returns:
            Status-String: "ok", "warning", "critical" oder "expired"
        """
        if days_remaining <= 0:
            return "expired"
        if days_remaining <= self.critical_days:
            return "critical"
        if days_remaining <= self.warning_days:
            return "warning"
        return "ok"

    async def check_certificate(self) -> dict:
        """
        Prueft das SSL-Zertifikat der konfigurierten Domain.

        Returns:
            Dict mit Keys:
                - domain (str): Gepruefete Domain
                - valid_until (str): Ablaufdatum als ISO-String
                - days_remaining (int): Verbleibende Tage
                - status (str): "ok", "warning", "critical" oder "expired"
                - issuer (str|None): Zertifikats-Aussteller
                - subject (str|None): Zertifikats-Subject
                - checked_at (str): Zeitpunkt der Pruefung als ISO-String
                - error (str|None): Fehlermeldung falls vorhanden
        """
        result: dict = {
            "domain": self.domain,
            "valid_until": None,
            "days_remaining": -1,
            "status": "error",
            "issuer": None,
            "subject": None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
        }

        try:
            # Zertifikat in einem Thread holen (blockierender Socket-Aufruf)
            cert = await asyncio.to_thread(self._fetch_certificate)

            # Ablaufdatum parsen
            not_after = cert.get("notAfter")
            if not not_after:
                result["error"] = "Kein Ablaufdatum im Zertifikat gefunden"
                logger.error("SSL-Check %s: %s", self.domain, result["error"])
                self._last_result = result
                return result

            expiry_date = self._parse_cert_date(not_after)
            now = datetime.now(timezone.utc)
            days_remaining = (expiry_date - now).days

            # Ergebnis zusammenbauen
            result["valid_until"] = expiry_date.isoformat()
            result["days_remaining"] = days_remaining
            result["status"] = self._determine_status(days_remaining)

            # Aussteller extrahieren (Tuple-of-Tuples Format)
            issuer = cert.get("issuer")
            if issuer:
                issuer_parts: list[str] = []
                for field in issuer:
                    for key, value in field:
                        if key in ("organizationName", "commonName"):
                            issuer_parts.append(value)
                result["issuer"] = " / ".join(issuer_parts) if issuer_parts else None

            # Subject extrahieren
            subject = cert.get("subject")
            if subject:
                for field in subject:
                    for key, value in field:
                        if key == "commonName":
                            result["subject"] = value
                            break

            # Log-Meldung je nach Status
            if result["status"] == "ok":
                logger.info(
                    "SSL-Check %s: OK — %d Tage verbleibend (laeuft ab am %s)",
                    self.domain, days_remaining, expiry_date.strftime("%Y-%m-%d")
                )
            elif result["status"] == "warning":
                logger.warning(
                    "SSL-Check %s: WARNUNG — Nur noch %d Tage gueltig! (Ablauf: %s)",
                    self.domain, days_remaining, expiry_date.strftime("%Y-%m-%d")
                )
            elif result["status"] == "critical":
                logger.error(
                    "SSL-Check %s: KRITISCH — Nur noch %d Tage gueltig! (Ablauf: %s)",
                    self.domain, days_remaining, expiry_date.strftime("%Y-%m-%d")
                )
            elif result["status"] == "expired":
                logger.error(
                    "SSL-Check %s: ABGELAUFEN seit %d Tagen! (Ablauf war: %s)",
                    self.domain, abs(days_remaining), expiry_date.strftime("%Y-%m-%d")
                )

        except ssl.SSLError as e:
            result["error"] = f"SSL-Fehler: {e}"
            logger.error("SSL-Check %s: %s", self.domain, result["error"])
        except ssl.SSLCertVerificationError as e:
            result["error"] = f"Zertifikat ungueltig: {e}"
            logger.error("SSL-Check %s: %s", self.domain, result["error"])
        except socket.timeout:
            result["error"] = f"Verbindungs-Timeout nach {SSL_TIMEOUT}s"
            logger.error("SSL-Check %s: %s", self.domain, result["error"])
        except socket.gaierror as e:
            result["error"] = f"DNS-Aufloesung fehlgeschlagen: {e}"
            logger.error("SSL-Check %s: %s", self.domain, result["error"])
        except ConnectionRefusedError:
            result["error"] = f"Verbindung abgelehnt auf Port {self.port}"
            logger.error("SSL-Check %s: %s", self.domain, result["error"])
        except OSError as e:
            result["error"] = f"Netzwerk-Fehler: {e}"
            logger.error("SSL-Check %s: %s", self.domain, result["error"])

        self._last_result = result
        return result

    def get_last_result(self) -> Optional[dict]:
        """Gibt das letzte Pruefergebnis zurueck (oder None wenn noch nicht geprueft)."""
        return self._last_result

    def is_expiring_soon(self) -> bool:
        """
        Schnell-Check ob das Zertifikat bald ablaeuft.

        Returns:
            True wenn Status "warning", "critical" oder "expired"
        """
        if self._last_result is None:
            return False
        return self._last_result.get("status") in ("warning", "critical", "expired")
