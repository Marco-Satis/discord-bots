"""
NeoForge Updater — Erkennt und aktualisiert NeoForge-Versionen

Prüft die NeoForge-Version im Server Pack und installiert bei Bedarf
den neuen NeoForge-Installer.

Versions-Erkennung:
  - `variables.txt` im Server Pack enthält NEOFORGE_VERSION
  - Alternativ: NeoForge-Installer JAR-Name (z.B. neoforge-21.1.220-installer.jar)

Installation:
  - java -jar neoforge-installer.jar --installServer
  - Verifizierung: run.sh wurde aktualisiert
"""

import asyncio
import re
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from utils.logger import get_logger

logger = get_logger("minecraft.neoforge_updater")

# Regex für NeoForge-Version aus verschiedenen Quellen
NEOFORGE_VERSION_RE = re.compile(r'NEOFORGE[_-]?VERSION[=:\s]+["\']?([\d.]+)["\']?', re.IGNORECASE)
NEOFORGE_JAR_RE = re.compile(r'neoforge[_-]([\d.]+)[_-]installer\.jar', re.IGNORECASE)
NEOFORGE_INSTALLER_URL = "https://maven.neoforged.net/releases/net/neoforged/neoforge/{version}/neoforge-{version}-installer.jar"


class NeoForgeUpdater:
    """
    Erkennt NeoForge-Versionsänderungen und führt Updates durch.

    Wird vom UpdateManager aufgerufen, wenn ein Modpack-Update
    eine neue NeoForge-Version enthält.
    """

    def __init__(self, server_path: Path, server_user: str = "minecraft"):
        self.server_path = server_path
        self.server_user = server_user

    async def detect_version_from_pack(
        self, extracted_dir: Path
    ) -> Optional[str]:
        """
        Erkennt die NeoForge-Version im entpackten Server Pack.

        Sucht in:
        1. variables.txt (BMC5 Standard)
        2. NeoForge-Installer JAR-Name
        3. run.sh / run.bat

        Args:
            extracted_dir: Pfad zum entpackten Server Pack

        Returns:
            NeoForge-Version (z.B. "21.1.220") oder None
        """
        # Methode 1: variables.txt
        variables_file = extracted_dir / "variables.txt"
        if variables_file.exists():
            try:
                content = variables_file.read_text()
                match = NEOFORGE_VERSION_RE.search(content)
                if match:
                    version = match.group(1)
                    logger.info(f"NeoForge-Version aus variables.txt: {version}")
                    return version
            except Exception as e:
                logger.debug(f"variables.txt nicht lesbar: {e}")

        # Methode 2: NeoForge-Installer JAR suchen
        for jar in extracted_dir.glob("*neoforge*installer*.jar"):
            match = NEOFORGE_JAR_RE.search(jar.name)
            if match:
                version = match.group(1)
                logger.info(f"NeoForge-Version aus Installer-JAR: {version}")
                return version

        # Methode 3: run.sh / run.bat parsen
        for script in ["run.sh", "run.bat"]:
            script_file = extracted_dir / script
            if script_file.exists():
                try:
                    content = script_file.read_text()
                    match = re.search(r'neoforge[_-]([\d.]+)', content)
                    if match:
                        version = match.group(1)
                        logger.info(f"NeoForge-Version aus {script}: {version}")
                        return version
                except Exception as e:
                    logger.debug(f"{script} nicht lesbar: {e}")

        logger.debug("Keine NeoForge-Version im Server Pack gefunden")
        return None

    async def get_installed_version(self) -> Optional[str]:
        """
        Liest die aktuell installierte NeoForge-Version.

        Prüft run.sh und libraries/ im Server-Verzeichnis.

        Returns:
            Installierte Version oder None
        """
        # run.sh parsen
        run_sh = self.server_path / "run.sh"
        if run_sh.exists():
            try:
                # sudo cat für Leserechte
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "-u", self.server_user, "cat", str(run_sh),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                if proc.returncode == 0:
                    content = stdout.decode("utf-8", errors="ignore")
                    match = re.search(r'neoforge[_-]([\d.]+)', content)
                    if match:
                        return match.group(1)
            except Exception as e:
                logger.debug(f"run.sh nicht lesbar: {e}")

        return None

    async def needs_update(
        self, extracted_dir: Path
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Prüft ob NeoForge aktualisiert werden muss.

        Args:
            extracted_dir: Entpacktes Server Pack

        Returns:
            (update_nötig, alte_version, neue_version)
        """
        new_version = await self.detect_version_from_pack(extracted_dir)
        if not new_version:
            return False, None, None

        old_version = await self.get_installed_version()
        if not old_version:
            logger.info("Keine installierte NeoForge-Version gefunden — Update wird durchgeführt")
            return True, None, new_version

        if old_version != new_version:
            logger.info(
                f"NeoForge-Update nötig: {old_version} → {new_version}"
            )
            return True, old_version, new_version

        logger.info(f"NeoForge ist aktuell: {old_version}")
        return False, old_version, new_version

    async def install(
        self,
        version: str,
        installer_jar: Optional[Path] = None,
    ) -> Tuple[bool, str]:
        """
        Installiert NeoForge via offiziellen Installer.

        Args:
            version: NeoForge-Version (z.B. "21.1.220")
            installer_jar: Pfad zum Installer (optional — wird heruntergeladen wenn nicht vorhanden)

        Returns:
            (erfolg, nachricht)
        """
        logger.info(f"NeoForge {version} Installation gestartet...")

        # Installer im Server Pack suchen oder herunterladen
        if not installer_jar or not installer_jar.exists():
            # Im Server-Verzeichnis suchen
            for jar in self.server_path.glob("*neoforge*installer*.jar"):
                if version in jar.name:
                    installer_jar = jar
                    break

        if not installer_jar or not installer_jar.exists():
            # Herunterladen
            installer_url = NEOFORGE_INSTALLER_URL.format(version=version)
            installer_jar = self.server_path / f"neoforge-{version}-installer.jar"

            logger.info(f"NeoForge-Installer wird heruntergeladen: {installer_url}")
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        installer_url,
                        timeout=aiohttp.ClientTimeout(total=300),
                    ) as resp:
                        if resp.status != 200:
                            return False, f"Installer-Download fehlgeschlagen: HTTP {resp.status}"
                        # BUG-3: Streaming-Download statt alles in RAM
                        with open(installer_jar, "wb") as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                f.write(chunk)
                        logger.info(f"Installer heruntergeladen: {len(content)} Bytes")
            except Exception as e:
                return False, f"Installer-Download fehlgeschlagen: {e}"

        # Installer als server_user ausführen
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-u", self.server_user,
                "java", "-jar", str(installer_jar), "--installServer",
                cwd=str(self.server_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            output = stdout.decode("utf-8", errors="ignore")
            err = stderr.decode("utf-8", errors="ignore")

            if proc.returncode != 0:
                return False, f"NeoForge-Installer Fehler (Code {proc.returncode}): {err[:300]}"

            # Verifizierung: run.sh wurde aktualisiert
            run_sh = self.server_path / "run.sh"
            if run_sh.exists():
                # Prüfen ob die neue Version in run.sh steht
                proc2 = await asyncio.create_subprocess_exec(
                    "sudo", "-u", self.server_user, "cat", str(run_sh),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=10)
                if version in stdout2.decode("utf-8", errors="ignore"):
                    logger.info(f"NeoForge {version} erfolgreich installiert und verifiziert")
                    return True, f"NeoForge {version} erfolgreich installiert"
                else:
                    logger.warning("run.sh enthält nicht die erwartete NeoForge-Version")
                    return True, f"NeoForge {version} installiert (run.sh-Verifikation unsicher)"
            else:
                logger.warning("run.sh nicht gefunden nach Installation")
                return True, f"NeoForge {version} installiert (run.sh nicht gefunden)"

        except asyncio.TimeoutError:
            return False, "NeoForge-Installer Timeout (>5 Minuten)"
        except Exception as e:
            return False, f"NeoForge-Installer Fehler: {e}"
