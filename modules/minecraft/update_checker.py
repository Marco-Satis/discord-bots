"""
Minecraft Paper Update Checker
Prueft auf neue Paper-Server-Builds via Paper API.
Unterstuetzt automatischen JAR-Download und Installation.

Paper API: https://api.papermc.io/v2/
"""

import asyncio
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable, Awaitable, Tuple, Dict, Any

from utils.logger import get_logger

logger = get_logger("minecraft.update_checker")

# Paper API Basis-URL
PAPER_API_BASE = "https://api.papermc.io/v2/projects/paper"


class MinecraftUpdateChecker:
    """
    Prueft auf Paper-Server-Updates via Paper API.

    Fuer Paper/Spigot-basierte Server.
    Nicht geeignet fuer NeoForge/Forge-Server (z.B. Better MC 5).

    Usage:
        checker = MinecraftUpdateChecker(server_path, server_id="BMC")
        checker.on_update_available = my_callback
        available, info = await checker.check()
    """

    def __init__(self,
                 server_path: Path,
                 server_id: str = "",
                 jar_name: str = "server.jar") -> None:
        """
        Args:
            server_path: Pfad zum Server-Verzeichnis
            server_id: Server-ID fuer Log-Prefix. Leer = erster konfigurierter
                Minecraft-Server. Frueher stand hier fest "VANILLA" — nach
                dessen Stilllegung ergab der Log-Prefix einen Server, den es
                nicht mehr gibt.
            jar_name: Name der Server-JAR-Datei
        """
        if not server_id:
            from modules.server_registry import vorgabe
            server_id = vorgabe("minecraft") or "MC"
        self.server_path = server_path
        self.server_id = server_id.upper()
        self.jar_name = jar_name

        # Status
        self.last_check: Optional[datetime] = None
        self.current_build: Optional[int] = None
        self.current_version: Optional[str] = None
        self.latest_build: Optional[int] = None
        self.update_available: bool = False

        # Callback: async def callback(current_build, latest_build, version)
        self.on_update_available: Optional[
            Callable[[int, int, str], Awaitable]
        ] = None

    # ------------------------------------------------------------------
    # Update pruefen
    # ------------------------------------------------------------------

    async def check(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Prueft ob ein Paper-Update verfuegbar ist.

        Returns:
            (update_verfuegbar, info_dict)
        """
        self.last_check = datetime.now()
        info: Dict[str, Any] = {
            "current_version": None,
            "current_build": None,
            "latest_build": None,
            "update_available": False,
            "download_url": None,
            "checked_at": self.last_check.isoformat(),
        }

        # Aktuelle Version ermitteln
        version, build = await self._get_installed_version()
        self.current_version = version
        self.current_build = build
        info["current_version"] = version
        info["current_build"] = build

        if not version:
            logger.warning(
                f"[{self.server_id}] MC-Version konnte nicht ermittelt werden"
            )
            return False, info

        # Neuesten Build von Paper API abfragen
        latest_build, download_url = await self._get_latest_build(version)
        self.latest_build = latest_build
        info["latest_build"] = latest_build
        info["download_url"] = download_url

        if build is not None and latest_build is not None and build < latest_build:
            self.update_available = True
            info["update_available"] = True
            logger.info(
                f"[{self.server_id}] Paper-Update verfuegbar! "
                f"Build {build} -> {latest_build} (MC {version})"
            )

            if self.on_update_available:
                try:
                    await self.on_update_available(build, latest_build, version)
                except Exception as e:
                    logger.error(f"[{self.server_id}] Update-Callback Fehler: {e}")
        else:
            self.update_available = False
            if build and latest_build:
                logger.debug(
                    f"[{self.server_id}] Kein Update — "
                    f"Build {build} ist aktuell (latest: {latest_build})"
                )

        return self.update_available, info

    # ------------------------------------------------------------------
    # Update durchfuehren
    # ------------------------------------------------------------------

    async def perform_update(self) -> Tuple[bool, str]:
        """
        Neuesten Paper-Build herunterladen und installieren.
        Server muss VOR dem Aufruf gestoppt werden!

        Returns:
            (erfolg, nachricht)
        """
        if not self.current_version or not self.latest_build:
            return False, "Kein Update-Check durchgefuehrt. Bitte zuerst pruefen."

        version = self.current_version
        build = self.latest_build

        # Download-URL zusammenbauen
        download_url = (
            f"{PAPER_API_BASE}/versions/{version}/builds/{build}/downloads/"
            f"paper-{version}-{build}.jar"
        )

        jar_path = self.server_path / self.jar_name
        new_jar = self.server_path / "server-new.jar"
        backup_jar = self.server_path / f"server-{datetime.now().strftime('%Y%m%d_%H%M%S')}.jar.bak"

        try:
            logger.info(
                f"[{self.server_id}] Paper-Update herunterladen: "
                f"Build {build} (MC {version})"
            )

            # JAR herunterladen mit curl (verfuegbar auf Linux)
            proc = await asyncio.create_subprocess_exec(
                "curl", "-fsSL", "-o", str(new_jar), download_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode != 0:
                error = stderr.decode("utf-8", errors="ignore")
                return False, f"Download fehlgeschlagen: {error[:200]}"

            # Pruefen ob Datei eine gueltige JAR ist (mindestens 1 MB)
            new_size = new_jar.stat().st_size
            if new_size < 1_000_000:
                new_jar.unlink()
                return False, f"Download ungueltig (nur {new_size} Bytes)"

            loop = asyncio.get_running_loop()

            # Aktuelles JAR sichern
            if jar_path.exists():
                await loop.run_in_executor(
                    None, shutil.copy2, jar_path, backup_jar
                )
                logger.info(
                    f"[{self.server_id}] Altes JAR gesichert: {backup_jar.name}"
                )

            # Neues JAR umbenennen
            await loop.run_in_executor(
                None, shutil.move, str(new_jar), str(jar_path)
            )

            self.update_available = False
            self.current_build = build
            size_mb = new_size / (1024 * 1024)
            msg = (
                f"Paper-Update installiert: Build {build} ({size_mb:.1f} MB)\n"
                f"⚠️ Server-Neustart erforderlich!"
            )
            logger.info(f"[{self.server_id}] {msg}")
            return True, msg

        except asyncio.TimeoutError:
            # Aufraumen
            if new_jar.exists():
                new_jar.unlink()
            return False, "Download-Timeout (>2 Minuten)"
        except Exception as e:
            # Aufraumen
            if new_jar.exists():
                try:
                    new_jar.unlink()
                except OSError:
                    pass
            logger.error(f"[{self.server_id}] Update fehlgeschlagen: {e}")
            return False, f"Update fehlgeschlagen: {e}"

    # ------------------------------------------------------------------
    # Versions-Erkennung
    # ------------------------------------------------------------------

    async def _get_installed_version(self) -> Tuple[Optional[str], Optional[int]]:
        """
        Ermittelt die installierte MC-Version und Paper-Build-Nummer.

        Liest zuerst version.json (von Paper erstellt), dann
        Fallback auf JAR-Manifest.

        Returns:
            (mc_version, build_number) — z.B. ("1.21.1", 69)
        """
        # Methode 1: version.json (Paper erstellt diese Datei)
        version_json = self.server_path / "version.json"
        if version_json.exists():
            try:
                data = json.loads(version_json.read_text(encoding="utf-8"))
                # Paper version.json: {"id": "1.21.1", "name": "1.21.1"}
                version = data.get("id") or data.get("name")
                if version:
                    # Build-Nummer aus dem Dateinamen oder Paper-spezifischer Datei
                    build = await self._get_build_from_paper_version()
                    return version, build
            except (json.JSONDecodeError, OSError) as e:
                logger.debug(
                    f"[{self.server_id}] version.json nicht lesbar: {e}"
                )

        # Methode 2: Paper-Version aus .paper-version oder JAR-Name
        paper_version = self.server_path / ".paper-version"
        if paper_version.exists():
            try:
                content = paper_version.read_text(encoding="utf-8").strip()
                # Format kann variieren, z.B. "1.21.1" oder "1.21.1-69"
                if "-" in content:
                    parts = content.rsplit("-", 1)
                    version = parts[0]
                    try:
                        build = int(parts[1])
                    except ValueError:
                        build = None
                    return version, build
                return content, None
            except OSError:
                pass

        # Methode 3: version_history.json (Paper legt das an)
        vh_path = self.server_path / "version_history.json"
        if vh_path.exists():
            try:
                data = json.loads(vh_path.read_text(encoding="utf-8"))
                current = data.get("currentVersion")
                if current:
                    # Format: "git-Paper-69 (MC: 1.21.1)"
                    import re
                    match = re.search(r"git-Paper-(\d+)\s+\(MC:\s*([\d.]+)\)", current)
                    if match:
                        return match.group(2), int(match.group(1))
                    # Alternatives Format
                    match2 = re.search(r"([\d.]+)", current)
                    if match2:
                        return match2.group(1), None
            except (json.JSONDecodeError, OSError) as e:
                logger.debug(f"[{self.server_id}] version_history.json Fehler: {e}")

        logger.warning(f"[{self.server_id}] Konnte MC-Version nicht ermitteln")
        return None, None

    async def _get_build_from_paper_version(self) -> Optional[int]:
        """Versucht die Paper-Build-Nummer aus version_history.json zu lesen"""
        vh_path = self.server_path / "version_history.json"
        if vh_path.exists():
            try:
                data = json.loads(vh_path.read_text(encoding="utf-8"))
                current = data.get("currentVersion", "")
                # Format: "git-Paper-69 (MC: 1.21.1)"
                import re
                match = re.search(r"git-Paper-(\d+)", current)
                if match:
                    return int(match.group(1))
            except (json.JSONDecodeError, OSError, ValueError):
                pass
        return None

    # ------------------------------------------------------------------
    # Paper API
    # ------------------------------------------------------------------

    async def _get_latest_build(self, version: str) -> Tuple[Optional[int], Optional[str]]:
        """
        Neuesten Paper-Build von der API abfragen.

        Args:
            version: MC-Version (z.B. "1.21.1")

        Returns:
            (build_nummer, download_url)
        """
        api_url = f"{PAPER_API_BASE}/versions/{version}/builds"

        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-fsSL", "--max-time", "15", api_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)

            if proc.returncode != 0:
                error = stderr.decode("utf-8", errors="ignore")
                logger.warning(
                    f"[{self.server_id}] Paper API Fehler: {error[:200]}"
                )
                return None, None

            data = json.loads(stdout.decode("utf-8"))
            builds = data.get("builds", [])
            if not builds:
                return None, None

            # Neuesten Build nehmen
            latest = builds[-1]
            build_num = latest.get("build")
            channel = latest.get("channel", "default")

            # Nur stabile Builds (nicht experimental)
            if channel == "experimental":
                # Letzten stabilen Build suchen
                for b in reversed(builds):
                    if b.get("channel", "default") != "experimental":
                        latest = b
                        build_num = b.get("build")
                        break

            if build_num is None:
                return None, None

            # Download-URL zusammenbauen
            downloads = latest.get("downloads", {})
            app_download = downloads.get("application", {})
            jar_name = app_download.get("name", f"paper-{version}-{build_num}.jar")
            download_url = (
                f"{PAPER_API_BASE}/versions/{version}/builds/{build_num}/"
                f"downloads/{jar_name}"
            )

            return build_num, download_url

        except asyncio.TimeoutError:
            logger.warning(f"[{self.server_id}] Paper API Timeout")
            return None, None
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"[{self.server_id}] Paper API Parsing-Fehler: {e}")
            return None, None
        except Exception as e:
            logger.error(f"[{self.server_id}] Paper API Fehler: {e}")
            return None, None
