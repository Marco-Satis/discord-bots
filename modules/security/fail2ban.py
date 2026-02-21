"""
F31: Fail2Ban Manager — Liest und verwaltet Fail2Ban-Bans.

Bietet asynchrone Methoden zum Abfragen des Fail2Ban-Status,
Auflisten gebannter IPs und Entbannen einzelner IPs.
Nutzt asyncio.create_subprocess_exec fuer fail2ban-client Aufrufe.
"""

import asyncio
import re
from typing import Optional

from utils.logger import get_logger
from utils.config import get_config, get_env, PROJECT_ROOT

logger = get_logger("security.fail2ban")

# Regex zum Parsen der fail2ban-client Ausgabe
RE_JAIL_LIST = re.compile(r"Jail list:\s*(.+)", re.IGNORECASE)
RE_BANNED_IPS = re.compile(r"Banned IP list:\s*(.*)", re.IGNORECASE)
RE_CURRENTLY_BANNED = re.compile(r"Currently banned:\s*(\d+)", re.IGNORECASE)
RE_TOTAL_BANNED = re.compile(r"Total banned:\s*(\d+)", re.IGNORECASE)
RE_CURRENTLY_FAILED = re.compile(r"Currently failed:\s*(\d+)", re.IGNORECASE)
RE_TOTAL_FAILED = re.compile(r"Total failed:\s*(\d+)", re.IGNORECASE)

# Timeout fuer Subprozesse (Sekunden)
SUBPROCESS_TIMEOUT: float = 10.0


class Fail2BanManager:
    """
    Verwaltet Fail2Ban-Interaktionen ueber fail2ban-client.

    Alle Aufrufe nutzen sudo fail2ban-client und sind async-kompatibel.
    Der ausfuehrende Benutzer muss sudo-Rechte fuer fail2ban-client haben.
    """

    def __init__(self, sudo: bool = True) -> None:
        """
        Args:
            sudo: Ob Befehle mit sudo ausgefuehrt werden (Standard: True)
        """
        self._sudo = sudo
        logger.info("Fail2BanManager initialisiert (sudo=%s)", sudo)

    def _build_command(self, *args: str) -> list[str]:
        """Erstellt den Befehl mit optionalem sudo-Prefix."""
        cmd: list[str] = []
        if self._sudo:
            cmd.append("sudo")
        cmd.append("fail2ban-client")
        cmd.extend(args)
        return cmd

    async def _run_command(self, *args: str) -> tuple[bool, str]:
        """
        Fuehrt einen fail2ban-client Befehl aus.

        Args:
            *args: Argumente fuer fail2ban-client

        Returns:
            Tuple (erfolg, stdout_text)
        """
        cmd = self._build_command(*args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=SUBPROCESS_TIMEOUT
            )

            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                logger.warning(
                    "fail2ban-client %s fehlgeschlagen (rc=%d): %s",
                    " ".join(args), proc.returncode, stderr_text
                )
                return False, stderr_text

            return True, stdout_text

        except asyncio.TimeoutError:
            logger.error("fail2ban-client %s: Timeout nach %.0fs", " ".join(args), SUBPROCESS_TIMEOUT)
            return False, "Timeout"
        except FileNotFoundError:
            logger.error("fail2ban-client nicht gefunden — ist Fail2Ban installiert?")
            return False, "fail2ban-client nicht gefunden"
        except OSError as e:
            logger.error("fail2ban-client Ausfuehrungsfehler: %s", e)
            return False, str(e)

    async def get_status(self) -> dict:
        """
        Liest den globalen Fail2Ban-Status inkl. aller Jails.

        Returns:
            Dict mit Keys:
                - running (bool): Ob Fail2Ban laeuft
                - jails (list[str]): Liste aktiver Jail-Namen
                - jail_count (int): Anzahl aktiver Jails
                - error (str|None): Fehlermeldung falls vorhanden
        """
        result: dict = {
            "running": False,
            "jails": [],
            "jail_count": 0,
            "error": None,
        }

        ok, output = await self._run_command("status")
        if not ok:
            result["error"] = output
            return result

        result["running"] = True

        # Jail-Liste extrahieren: "Jail list:   sshd, nginx-http-auth"
        match = RE_JAIL_LIST.search(output)
        if match:
            jail_str = match.group(1).strip()
            if jail_str:
                jails = [j.strip() for j in jail_str.split(",") if j.strip()]
                result["jails"] = jails
                result["jail_count"] = len(jails)

        logger.debug("Fail2Ban Status: %d Jails aktiv", result["jail_count"])
        return result

    async def get_jail_status(self, jail: str) -> dict:
        """
        Liest den detaillierten Status eines einzelnen Jails.

        Args:
            jail: Name des Jails (z.B. "sshd")

        Returns:
            Dict mit Keys:
                - jail (str): Jail-Name
                - currently_failed (int): Aktuell fehlgeschlagene Versuche
                - total_failed (int): Gesamt fehlgeschlagene Versuche
                - currently_banned (int): Aktuell gebannte IPs
                - total_banned (int): Gesamt gebannte IPs
                - banned_ips (list[str]): Liste gebannter IP-Adressen
                - error (str|None): Fehlermeldung falls vorhanden
        """
        result: dict = {
            "jail": jail,
            "currently_failed": 0,
            "total_failed": 0,
            "currently_banned": 0,
            "total_banned": 0,
            "banned_ips": [],
            "error": None,
        }

        ok, output = await self._run_command("status", jail)
        if not ok:
            result["error"] = output
            return result

        # Zahlen extrahieren
        match = RE_CURRENTLY_FAILED.search(output)
        if match:
            result["currently_failed"] = int(match.group(1))

        match = RE_TOTAL_FAILED.search(output)
        if match:
            result["total_failed"] = int(match.group(1))

        match = RE_CURRENTLY_BANNED.search(output)
        if match:
            result["currently_banned"] = int(match.group(1))

        match = RE_TOTAL_BANNED.search(output)
        if match:
            result["total_banned"] = int(match.group(1))

        # Gebannte IPs extrahieren
        match = RE_BANNED_IPS.search(output)
        if match:
            ip_str = match.group(1).strip()
            if ip_str:
                ips = [ip.strip() for ip in ip_str.split() if ip.strip()]
                result["banned_ips"] = ips

        logger.debug(
            "Jail '%s': %d gebannt, %d fehlgeschlagen",
            jail, result["currently_banned"], result["currently_failed"]
        )
        return result

    async def get_banned_ips(self) -> list[dict]:
        """
        Sammelt alle gebannten IPs aus allen aktiven Jails.

        Returns:
            Liste von Dicts mit Keys:
                - ip (str): Die gebannte IP-Adresse
                - jail (str): Name des Jails
                - currently_banned (int): Anzahl aktueller Bans im Jail
                - total_banned (int): Gesamt-Bans im Jail
        """
        banned: list[dict] = []

        # Zuerst globalen Status fuer Jail-Liste holen
        status = await self.get_status()
        if not status["running"] or not status["jails"]:
            if status.get("error"):
                logger.warning("Kann gebannte IPs nicht lesen: %s", status["error"])
            return banned

        # Jeden Jail einzeln abfragen
        for jail in status["jails"]:
            jail_status = await self.get_jail_status(jail)
            if jail_status.get("error"):
                continue

            for ip in jail_status["banned_ips"]:
                banned.append({
                    "ip": ip,
                    "jail": jail,
                    "currently_banned": jail_status["currently_banned"],
                    "total_banned": jail_status["total_banned"],
                })

        logger.info("Gesamt gebannte IPs: %d (ueber %d Jails)", len(banned), len(status["jails"]))
        return banned

    async def unban_ip(self, jail: str, ip: str) -> bool:
        """
        Entbannt eine IP-Adresse aus einem bestimmten Jail.

        Args:
            jail: Name des Jails (z.B. "sshd")
            ip: Die zu entbannende IP-Adresse

        Returns:
            True wenn erfolgreich, False bei Fehler
        """
        # Eingabe-Validierung
        if not jail or not jail.strip():
            logger.error("Unban fehlgeschlagen: Leerer Jail-Name")
            return False

        if not ip or not ip.strip():
            logger.error("Unban fehlgeschlagen: Leere IP-Adresse")
            return False

        # Einfache IP-Validierung (IPv4)
        ip = ip.strip()
        jail = jail.strip()

        ok, output = await self._run_command("set", jail, "unbanip", ip)
        if ok:
            logger.info("IP %s aus Jail '%s' entbannt", ip, jail)
            return True

        logger.warning("Entbannen von %s aus '%s' fehlgeschlagen: %s", ip, jail, output)
        return False

    async def unban_ip_all_jails(self, ip: str) -> dict:
        """
        Entbannt eine IP-Adresse aus allen Jails in denen sie gebannt ist.

        Args:
            ip: Die zu entbannende IP-Adresse

        Returns:
            Dict mit Keys:
                - ip (str): Die IP-Adresse
                - unbanned_from (list[str]): Jails aus denen entbannt wurde
                - failed (list[str]): Jails bei denen Entbannen fehlschlug
        """
        result: dict = {
            "ip": ip,
            "unbanned_from": [],
            "failed": [],
        }

        status = await self.get_status()
        if not status["running"]:
            return result

        for jail in status["jails"]:
            jail_info = await self.get_jail_status(jail)
            if ip in jail_info.get("banned_ips", []):
                success = await self.unban_ip(jail, ip)
                if success:
                    result["unbanned_from"].append(jail)
                else:
                    result["failed"].append(jail)

        return result
