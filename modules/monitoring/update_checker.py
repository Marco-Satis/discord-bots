"""
Update Checker Module - SteamCMD Update Detection
Checks for Satisfactory Dedicated Server updates via SteamCMD
"""

import asyncio
import subprocess
import re
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable, Tuple, Dict, Any
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("update_checker")

# Satisfactory Dedicated Server Steam App ID
SATISFACTORY_APP_ID = "1690800"


def _steamcmd_error_summary(returncode: int, stdout: str, stderr: str) -> str:
    """Zieht die aussagekraeftige Fehlerzeile aus dem SteamCMD-Output.

    Vorher wurde stumpf `stderr[:300]` gemeldet. Auf stderr steht bei jedem Lauf
    aber nur die harmlose Relaunch-Zeile ("Starting /home/.../linux32/steamcmd"),
    weshalb im Dashboard und in Discord eine Nicht-Meldung als Fehlerursache
    stand (beobachtet 2026-08-11). Die echte Ursache steht auf stdout.
    """
    interesting = [
        line.strip()
        for line in (stdout or "").splitlines()
        if re.search(r"error|failed|fehlgeschlagen|invalid|no subscription|disk",
                     line, re.IGNORECASE)
    ]
    if interesting:
        return " | ".join(interesting[-3:])[:300]

    if returncode == 8:
        return (
            "SteamCMD hat sich selbst aktualisiert und beide Versuche abgebrochen "
            "(Code 8). Der naechste Lauf nutzt das aktualisierte SteamCMD."
        )

    tail = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
    if tail:
        return " | ".join(tail[-3:])[:300]
    return (stderr or "").strip()[:300] or "keine Ausgabe"


class UpdateChecker:
    """
    Checks for game server updates using SteamCMD.

    Usage:
        checker = UpdateChecker(steamcmd_path="/usr/games/steamcmd")
        checker.on_update_available = my_callback
        available, info = await checker.check()
    """

    def __init__(self, steamcmd_path: str = "/usr/games/steamcmd",
                 install_dir: str = "/home/satisfactory/SatisfactoryDedicatedServer",
                 app_id: str = SATISFACTORY_APP_ID,
                 server_user: str = "satisfactory") -> None:
        self.steamcmd: str = steamcmd_path
        self.install_dir: str = install_dir
        self.app_id: str = app_id
        self.server_user: str = server_user

        self.last_check: Optional[datetime] = None
        self.last_known_buildid: Optional[str] = None
        self.installed_buildid: Optional[str] = None
        self.update_available: bool = False

        # Callback: async def callback(installed_build, available_build)
        self.on_update_available: Optional[Callable[[str, str], Awaitable]] = None
        # Build, fuer die zuletzt benachrichtigt wurde (Notify-Dedup gegen Spam
        # durch parallele Checker/Loops). Reset wenn kein Update mehr verfuegbar.
        self._notified_buildid: Optional[str] = None
        # True wenn perform_update den manual_stop-Marker SELBST gesetzt hat.
        # _safe_start cleart den Marker nur dann -> verhindert Loeschen eines
        # manuell von Marco gesetzten Stop-Markers (L1).
        self._update_marked_stop: bool = False
        # perform_update hat ZWEI Aufrufer: den Scheduler (Auto-Update) und
        # /update start aus dem Discord-Cog. Am 2026-08-11 liefen beide 22
        # Sekunden versetzt los und starteten zwei parallele SteamCMD-Prozesse
        # auf demselben Install-Verzeichnis; der zweite Lauf hat dem ersten
        # den Server unter den Fuessen weggestartet ("Server Start nach Update
        # fehlgeschlagen: Server laeuft bereits"). Der Lock serialisiert das.
        self._update_lock: asyncio.Lock = asyncio.Lock()

    async def check(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Check if an update is available.
        Returns (update_available, info_dict)
        """
        self.last_check = datetime.now()
        info = {
            "installed_buildid": None,
            "available_buildid": None,
            "update_available": False,
            "checked_at": self.last_check.isoformat(),
        }

        # Get installed build ID
        installed = await self._get_installed_buildid()
        info["installed_buildid"] = installed
        self.installed_buildid = installed

        # Get available build ID from Steam
        available = await self._get_available_buildid()
        info["available_buildid"] = available
        self.last_known_buildid = available

        if installed and available and installed != available:
            self.update_available = True
            info["update_available"] = True

            # Notify nur bei NEUER verfuegbarer Build (State-Change), nicht bei
            # jedem Check -> verhindert Mehrfach-Spam durch parallele Checker/Loops.
            if available != self._notified_buildid:
                logger.info(
                    f"Update available! Installed: {installed}, Available: {available}"
                )
                self._notified_buildid = available
                if self.on_update_available:
                    try:
                        await self.on_update_available(installed, available)
                    except Exception as e:
                        logger.error(f"Update callback error: {e}")
        else:
            self.update_available = False
            self._notified_buildid = None

        return self.update_available, info

    async def _get_installed_buildid(self) -> Optional[str]:
        """Read installed build ID from Steam appmanifest"""
        manifest_name = f"appmanifest_{self.app_id}.acf"

        # Possible manifest locations
        search_paths = [
            Path(self.install_dir) / "steamapps" / manifest_name,
            Path(self.install_dir).parent / "steamapps" / manifest_name,
            Path(f"/home/{self.server_user}/.steam/steamapps/{manifest_name}"),
            Path(f"/home/{self.server_user}/Steam/steamapps/{manifest_name}"),
        ]

        # Try direct read first (works if botuser has permission)
        for path in search_paths:
            try:
                resolved = path.resolve()
                if resolved.exists():
                    content = resolved.read_text()
                    match = re.search(r'"buildid"\s+"(\d+)"', content)
                    if match:
                        return match.group(1)
            except PermissionError:
                # Try with sudo
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "sudo", "-u", self.server_user, "cat", str(path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                    if proc.returncode == 0:
                        content = stdout.decode("utf-8", errors="ignore")
                        match = re.search(r'"buildid"\s+"(\d+)"', content)
                        if match:
                            return match.group(1)
                except (subprocess.SubprocessError, ValueError, OSError, asyncio.TimeoutError) as e:
                    logger.debug(f"Subprocess error reading {path}: {e}")
                    continue
            except (subprocess.SubprocessError, ValueError, OSError, asyncio.TimeoutError) as e:
                logger.debug(f"Error reading {path}: {e}")
                continue

        # Fallback: read via sudo cat for all paths
        for path in search_paths:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "-u", self.server_user, "cat", str(path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                if proc.returncode == 0:
                    content = stdout.decode("utf-8", errors="ignore")
                    match = re.search(r'"buildid"\s+"(\d+)"', content)
                    if match:
                        return match.group(1)
            except (subprocess.SubprocessError, ValueError, OSError, asyncio.TimeoutError) as e:
                logger.debug(f"Fallback read failed for {path}: {e}")
                continue

        return None

    async def _get_available_buildid(self) -> Optional[str]:
        """Check Steam for the latest available build ID via SteamCMD"""
        try:
            # SteamCMD braucht ein beschreibbares HOME-Verzeichnis fuer Cache/Logs.
            # /home/botuser ist read-only, deshalb /tmp/steamcmd_home verwenden.
            import os
            env = os.environ.copy()
            steam_home = Path("/tmp/steamcmd_home")  # nosec B108 - botuser-HOME ist read-only, intentional /tmp
            steam_home.mkdir(parents=True, exist_ok=True)
            env["HOME"] = str(steam_home)
            # Steam-Verzeichnisse sicherstellen
            steam_dir = steam_home / "Steam"
            steam_dir.mkdir(parents=True, exist_ok=True)
            (steam_dir / "logs").mkdir(parents=True, exist_ok=True)
            # .steam Symlinks erstellen falls nicht vorhanden
            dot_steam = steam_home / ".steam"
            dot_steam.mkdir(parents=True, exist_ok=True)
            for link_name in ("root", "steam"):
                link_path = dot_steam / link_name
                if not link_path.exists():
                    try:
                        link_path.symlink_to(steam_dir)
                    except OSError:
                        pass

            logger.debug(f"SteamCMD starten mit HOME={steam_home}")

            proc = await asyncio.create_subprocess_exec(
                self.steamcmd,
                "+login", "anonymous",
                "+app_info_update", "1",
                "+app_info_print", self.app_id,
                "+quit",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            output = stdout.decode("utf-8", errors="ignore")
            stderr_text = stderr.decode("utf-8", errors="ignore")

            if proc.returncode != 0:
                logger.warning(f"SteamCMD Exit-Code: {proc.returncode}")
                if stderr_text:
                    logger.debug(f"SteamCMD stderr: {stderr_text[:500]}")

            # Find the public branch buildid
            # Look for "branches" section, then "public" branch
            in_branches = False
            in_public = False
            for line in output.split("\n"):
                stripped = line.strip()
                if '"branches"' in stripped:
                    in_branches = True
                elif in_branches and '"public"' in stripped:
                    in_public = True
                elif in_public and '"buildid"' in stripped:
                    match = re.search(r'"buildid"\s+"(\d+)"', stripped)
                    if match:
                        return match.group(1)
                elif in_public and stripped == "}":
                    break

        except asyncio.TimeoutError:
            logger.warning("SteamCMD update check timed out")
        except Exception as e:
            logger.error(f"SteamCMD update check failed: {e}")

        return None

    async def perform_update(self, server: Any = None,
                             har: Any = None) -> Tuple[bool, str]:
        """Serialisierender Wrapper um den eigentlichen Update-Lauf.

        Zwei parallele Laeufe wuerden zwei SteamCMD-Prozesse auf dasselbe
        Install-Verzeichnis loslassen und sich gegenseitig den Server starten
        und stoppen. Der zweite Aufruf wartet deshalb nicht, sondern bekommt
        eine klare Absage — sonst haengt ein Discord-Command minutenlang.
        """
        if self._update_lock.locked():
            logger.warning("Update-Anfrage abgelehnt — es laeuft bereits ein Update")
            return False, "Es laeuft bereits ein Update. Bitte warten."

        async with self._update_lock:
            return await self._perform_update_locked(server, har)

    async def _perform_update_locked(self, server: Any = None,
                                     har: Any = None) -> Tuple[bool, str]:
        """
        Vollständiges SAT-Update via SteamCMD.

        Ablauf:
        1. HAR unterdrücken (falls übergeben)
        2. Prüfen ob Server gestoppt ist
        3. SteamCMD app_update ausführen
        4. Exit-Code UND Output prüfen
        5. Build-ID nach Update verifizieren
        6. Server starten
        7. Health-Check (is_running)

        Args:
            server: SatisfactoryServer-Instanz (optional, für Stop/Start)
            har: HealthAutoRestart-Instanz (optional, für Suppress)

        Returns:
            (success, message)
        """
        logger.info("SAT-Update wird durchgeführt...")

        old_buildid = self.installed_buildid

        try:
            # Schritt 1: HAR unterdrücken
            if har and hasattr(har, "suppress"):
                har.suppress("sat", "main", duration_seconds=900)  # 15 Minuten
                logger.info("Health-Auto-Restart für 15 Min unterdrückt")

            # service_watchdog/Daily-Restart blocken waehrend des Updates, damit
            # SAT nicht mitten im steamcmd-Lauf neugestartet wird (Race).
            # Lazy-Import gegen Circular-Import (update_checker liegt in modules.monitoring).
            if server:
                from modules.monitoring import manual_stop_state
                await manual_stop_state.mark_stopped("satisfactory")
                self._update_marked_stop = True

            # Schritt 2: Server stoppen (falls noch aktiv)
            if server and hasattr(server, "is_running"):
                try:
                    is_running = await server.is_running()
                    if is_running:
                        logger.info("SAT-Server wird gestoppt...")
                        if hasattr(server, "stop"):
                            await server.stop()
                        # Warten bis gestoppt
                        for _ in range(15):
                            await asyncio.sleep(2)
                            if not await server.is_running():
                                break
                        else:
                            logger.warning("SAT-Server nach 30s noch aktiv")
                except Exception as e:
                    logger.warning(f"Server-Stop Fehler (wird fortgesetzt): {e}")

            # Schritt 3: SteamCMD ausführen
            import os
            env = os.environ.copy()
            steam_home = Path("/tmp/steamcmd_home")  # nosec B108 - botuser-HOME read-only
            steam_home.mkdir(parents=True, exist_ok=True)
            env["HOME"] = str(steam_home)

            cmd = [
                "sudo", "-u", self.server_user,
                self.steamcmd,
                "+force_install_dir", self.install_dir,
                "+login", "anonymous",
                # app_info_update 1 erzwingt frische Steam-Metadaten (analog
                # _get_available_buildid). Ohne dies nutzt steamcmd-as-satisfactory
                # einen veralteten appinfo-Cache und meldet faelschlich "bereits
                # aktuell" -> Build erreicht nie die verfuegbare Version -> Update-Loop.
                "+app_info_update", "1",
                "+app_update", self.app_id, "validate",
                "+quit",
            ]

            logger.info(f"SteamCMD wird ausgeführt: {' '.join(cmd[3:])}")

            # SteamCMD bis zu 2x versuchen: endet der erste Lauf mit Code 8
            # (steamcmd.sh aktualisiert sich selbst und exitet dann 8 OHNE das
            # app_update auszufuehren), nutzt der zweite Lauf das aktualisierte
            # steamcmd und fuehrt das Update korrekt aus.
            output = ""
            err_output = ""
            for _attempt in range(2):
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
                output = stdout.decode("utf-8", errors="ignore")
                err_output = stderr.decode("utf-8", errors="ignore")
                if proc.returncode != 8:
                    break
                logger.warning(
                    "SteamCMD Code 8 (vermutlich Selbstupdate-Relaunch) - erneuter Versuch"
                )

            # Schritt 4: Exit-Code UND Output prüfen
            if proc.returncode != 0:
                error_msg = _steamcmd_error_summary(
                    proc.returncode, output, err_output
                )
                logger.error(f"SteamCMD Fehler (Code {proc.returncode}): {error_msg}")
                # Server trotzdem starten
                await self._safe_start(server)
                return False, f"SteamCMD Fehler (Code {proc.returncode}): {error_msg}"

            # Output-Analyse
            output_lower = output.lower()
            if "already up to date" in output_lower:
                logger.info("SAT-Server ist bereits aktuell")
                await self._safe_start(server)
                return True, "Server ist bereits aktuell"

            update_success = (
                "success" in output_lower
                or "fully installed" in output_lower
                or proc.returncode == 0
            )

            if not update_success:
                logger.warning(f"SteamCMD-Output unklar: {output[:200]}")

            # Schritt 5: Build-ID nach Update verifizieren
            new_buildid = await self._get_installed_buildid()
            if new_buildid and old_buildid and new_buildid != old_buildid:
                logger.info(
                    f"Build-ID geändert: {old_buildid} → {new_buildid}"
                )
                self.installed_buildid = new_buildid
                self.update_available = False
            elif new_buildid:
                self.installed_buildid = new_buildid

            # Harden (Loop-Guard): Ist ein neuerer Ziel-Build bekannt, der nach
            # app_update NICHT erreicht wurde, hat das Update nicht gegriffen.
            # Ohne diese Pruefung meldet perform_update faelschlich Erfolg, der
            # Build bleibt alt -> Auto-Update retryt endlos (Loop 2026-06-25).
            target: Optional[str] = self.last_known_buildid
            if (target and new_buildid and old_buildid
                    and old_buildid != target and new_buildid != target):
                logger.error(
                    f"Update nicht installiert: Build weiterhin {new_buildid}, "
                    f"Ziel {target} nicht erreicht."
                )
                await self._safe_start(server)
                return False, f"Update nicht installiert (Build {new_buildid}, Ziel {target})"

            # Schritt 6+7: Server starten + Health-Check
            if server:
                start_ok = await self._safe_start(server)
                if start_ok:
                    msg = f"Update erfolgreich (Build-ID: {new_buildid or '?'})"
                    logger.info(msg)
                    return True, msg
                else:
                    msg = "SteamCMD erfolgreich, aber Server startet nicht"
                    logger.warning(msg)
                    return False, msg
            else:
                return True, f"SteamCMD erfolgreich (Build-ID: {new_buildid or '?'})"

        except asyncio.TimeoutError:
            logger.error("SteamCMD Timeout (>10 Minuten)")
            await self._safe_start(server)
            return False, "Update Timeout (>10 Minuten)"
        except Exception as e:
            logger.error(f"Update Fehler: {e}")
            await self._safe_start(server)
            return False, f"Update Fehler: {str(e)}"

    async def _safe_start(self, server: Any = None) -> bool:
        """Versucht den Server zu starten (best-effort, max 90s Timeout)."""
        # Update-Fenster beendet -> manual_stop-Markierung loeschen. Jeder
        # Restart-/Except-Pfad ruft _safe_start -> zentraler Clear-Punkt
        # (verhindert haengenden "manually-stopped"-Marker). ABER nur wenn
        # DIESES Update den Marker selbst gesetzt hat (L1): sonst wuerde ein
        # manuell von Marco gesetzter Stop-Marker bei server=None-Except-Pfad
        # faelschlich geloescht.
        if self._update_marked_stop:
            try:
                from modules.monitoring import manual_stop_state
                await manual_stop_state.mark_started("satisfactory")
            except Exception as e:
                logger.warning(f"mark_started (manual_stop-Clear) Fehler: {e}")
            finally:
                self._update_marked_stop = False
        if not server or not hasattr(server, "start"):
            return False
        try:
            async def _start_and_wait() -> bool:
                await server.start()
                for _ in range(30):
                    await asyncio.sleep(2)
                    if hasattr(server, "is_running") and await server.is_running():
                        return True
                return False
            return await asyncio.wait_for(_start_and_wait(), timeout=90)
        except asyncio.TimeoutError:
            logger.error("Server-Start Timeout nach 90 Sekunden")
            return False
        except Exception as e:
            logger.error(f"Server-Start fehlgeschlagen: {e}")
            return False
