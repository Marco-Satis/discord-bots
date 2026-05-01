"""
NeoForge Updater — Erkennt NeoForge-Versionen und stellt Startfähigkeit sicher

Versions-Erkennung:
  - `variables.txt` im Server Pack enthält MODLOADER_VERSION
  - Alternativ: NeoForge-Installer JAR-Name
  - Alternativ: run.sh / start.sh

Startup-Sicherstellung:
  - Neue BMC5 Server Packs nutzen start.sh (ServerPackCreator)
  - Falls run.sh fehlt aber start.sh existiert: run.sh Wrapper erstellen
  - variables.txt wird für nicht-interaktiven Betrieb angepasst
"""

import asyncio
import re
from pathlib import Path
from typing import Optional, Tuple

from utils.logger import get_logger

logger = get_logger("minecraft.neoforge_updater")

# Regex für NeoForge-Version — matcht MODLOADER_VERSION und NEOFORGE_VERSION
MODLOADER_VERSION_RE = re.compile(
    r'MODLOADER_VERSION[=:\s]+["\']?([\d.]+)["\']?', re.IGNORECASE
)
NEOFORGE_VERSION_RE = re.compile(
    r'NEOFORGE[_-]?VERSION[=:\s]+["\']?([\d.]+)["\']?', re.IGNORECASE
)
NEOFORGE_JAR_RE = re.compile(
    r'neoforge[_-]([\d.]+)[_-]installer\.jar', re.IGNORECASE
)

# Nur gebraucht falls Installer-Methode nötig
NEOFORGE_INSTALLER_URL = (
    "https://maven.neoforged.net/releases/net/neoforged/neoforge/"
    "{version}/neoforge-{version}-installer.jar"
)


class NeoForgeUpdater:
    """
    Erkennt NeoForge-Versionsänderungen und stellt Server-Startfähigkeit sicher.

    Unterstützt zwei Startup-Methoden:
    1. ServerPackCreator (start.sh + variables.txt) — BMC5 v48+
    2. NeoForge Installer (run.sh generiert) — ältere Packs
    """

    def __init__(self, server_path: Path, server_user: str = "minecraft"):
        self.server_path = server_path
        self.server_user = server_user

    # ------------------------------------------------------------------
    # Versions-Erkennung
    # ------------------------------------------------------------------

    async def detect_version_from_pack(
        self, extracted_dir: Path
    ) -> Optional[str]:
        """
        Erkennt die NeoForge-Version im entpackten Server Pack.

        Sucht in:
        1. variables.txt → MODLOADER_VERSION (BMC5 Standard)
        2. variables.txt → NEOFORGE_VERSION (Fallback)
        3. NeoForge-Installer JAR-Name
        4. run.sh / start.sh

        Returns:
            NeoForge-Version (z.B. "21.1.219") oder None
        """
        # Methode 1: variables.txt — MODLOADER_VERSION (Standard für BMC5)
        variables_file = extracted_dir / "variables.txt"
        if variables_file.exists():
            try:
                content = variables_file.read_text()

                # Prüfen ob es NeoForge ist (MODLOADER=NeoForge)
                if re.search(r'MODLOADER\s*=\s*NeoForge', content, re.IGNORECASE):
                    match = MODLOADER_VERSION_RE.search(content)
                    if match:
                        version = match.group(1)
                        logger.info(f"NeoForge-Version aus variables.txt (MODLOADER_VERSION): {version}")
                        return version

                # Fallback: NEOFORGE_VERSION
                match = NEOFORGE_VERSION_RE.search(content)
                if match:
                    version = match.group(1)
                    logger.info(f"NeoForge-Version aus variables.txt (NEOFORGE_VERSION): {version}")
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

        # Methode 3: run.sh / start.sh parsen
        for script in ["run.sh", "start.sh", "run.bat", "start.bat"]:
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

        logger.warning("Keine NeoForge-Version im Server Pack gefunden")
        return None

    async def get_installed_version(self) -> Optional[str]:
        """
        Liest die aktuell installierte NeoForge-Version.

        Prüft variables.txt, run.sh und libraries/.

        Returns:
            Installierte Version oder None
        """
        # variables.txt prüfen (bevorzugt)
        variables_file = self.server_path / "variables.txt"
        if variables_file.exists():
            try:
                content = variables_file.read_text()
                if re.search(r'MODLOADER\s*=\s*NeoForge', content, re.IGNORECASE):
                    match = MODLOADER_VERSION_RE.search(content)
                    if match:
                        return match.group(1)
            except Exception as e:
                logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

        # run.sh parsen (Fallback)
        run_sh = self.server_path / "run.sh"
        if run_sh.exists():
            try:
                content = run_sh.read_text()
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
            logger.info(f"NeoForge-Update nötig: {old_version} → {new_version}")
            return True, old_version, new_version

        logger.info(f"NeoForge ist aktuell: {old_version}")
        return False, old_version, new_version

    # ------------------------------------------------------------------
    # Server-Startfähigkeit sicherstellen
    # ------------------------------------------------------------------

    async def ensure_startup_script(self) -> Tuple[bool, str]:
        """
        Stellt sicher dass der Server gestartet werden kann.

        Neue BMC5 Server Packs (v48+) nutzen start.sh statt run.sh.
        ServerPackCreator's start.sh → lädt ServerStarterJar (server.jar) →
        server.jar installiert NeoForge und erstellt run.sh → startet MC Server.

        WICHTIG: Kein run.sh Wrapper erstellen! server.jar PARST run.sh
        um Java-Startargumente zu finden. Ein Wrapper würde das brechen.
        Stattdessen: systemd ExecStart muss auf start.sh zeigen.

        Returns:
            (erfolg, nachricht)
        """
        run_sh = self.server_path / "run.sh"
        start_sh = self.server_path / "start.sh"

        # Wenn start.sh existiert → ServerPackCreator-Pack
        if start_sh.exists():
            logger.info("start.sh gefunden (ServerPackCreator) — patche für systemd")

            try:
                # Schritt 1: variables.txt für nicht-interaktiven Betrieb anpassen
                await self._patch_variables_txt()

                # Schritt 2: eula.txt sicherstellen
                eula_file = self.server_path / "eula.txt"
                await self._sudo_write_file(
                    eula_file,
                    "#By changing the setting below to TRUE you are indicating your agreement "
                    "to our EULA (https://aka.ms/MinecraftEULA).\neula=true\n",
                )
                logger.info("eula.txt mit eula=true geschrieben")

                # Schritt 3: start.sh EULA-Check patchen (interaktiver Prompt umgehen)
                await self._patch_start_sh_eula(start_sh)

                # KEIN run.sh Wrapper! server.jar (ServerStarterJar) parst run.sh
                # um Java-Startargumente zu finden. systemd muss start.sh direkt aufrufen.
                # Alte run.sh (egal ob Wrapper oder sonstwas) immer löschen —
                # server.jar erstellt beim NeoForge-Install seine eigene run.sh
                if run_sh.exists():
                    proc = await asyncio.create_subprocess_exec(
                        "sudo", "rm", "-f", str(run_sh),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=10)
                    logger.info("Alte run.sh entfernt (server.jar erstellt eigene)")

                logger.info("start.sh für systemd vorbereitet (EULA + variables gepatcht)")
                return True, "start.sh gepatcht (systemd ExecStart muss auf start.sh zeigen)"

            except Exception as e:
                logger.error(f"start.sh Vorbereitung fehlgeschlagen: {e}")
                return False, f"Startup-Script Fehler: {e}"

        # Legacy: run.sh existiert → altes Pack (NeoForge Installer)
        if run_sh.exists():
            logger.info("run.sh existiert — Server sollte startfähig sein")
            return True, "run.sh vorhanden"

        logger.error("Weder run.sh noch start.sh gefunden!")
        return False, "Kein Startup-Script gefunden (weder run.sh noch start.sh)"

    async def _sudo_write_file(self, target: Path, content: str) -> None:
        """
        Schreibt eine Datei in minecraft-owned Verzeichnisse.

        Strategie: In Staging schreiben (botuser-owned), dann sudo mv zum Ziel.
        sudo mv /home/minecraft/.update_staging/* → /home/minecraft/* ist in sudoers erlaubt.
        """
        staging_dir = Path("/home/minecraft/.update_staging")
        staging_dir.mkdir(parents=True, exist_ok=True)
        tmp_file = staging_dir / f"_tmp_{target.name}"

        try:
            import os as _os

            # Schritt 1: In Staging schreiben (botuser hat hier Schreibrechte)
            tmp_file.write_text(content)
            # Exec-Bit setzen (vor dem Copy, wird mitübernommen)
            _os.chmod(tmp_file, 0o755)

            # Schritt 2: sudo cp zum Ziel (sudoers: /bin/cp /home/minecraft/*)
            proc = await asyncio.create_subprocess_exec(
                "sudo", "cp",
                str(tmp_file), str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="ignore")
                raise OSError(f"sudo cp fehlgeschlagen: {err}")

            # Schritt 3: Ownership setzen
            proc = await asyncio.create_subprocess_exec(
                "sudo", "chown", "-R",
                f"{self.server_user}:{self.server_user}", str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)

        finally:
            # Temp-Datei aufräumen
            if tmp_file.exists():
                tmp_file.unlink()

    async def _patch_start_sh_eula(self, start_sh: Path) -> None:
        """
        Patcht start.sh um den interaktiven EULA-Check zu umgehen.

        ServerPackCreator 7.x prüft 'if [[ ! -s "eula.txt" ]]' und fragt dann
        interaktiv nach "type I agree". Unter systemd geht das nicht.
        Fix: EULA-Check durch automatische Akzeptanz ersetzen.
        """
        try:
            content = start_sh.read_text()
            original = content

            # Ersetze den EULA-Check: statt interaktiv zu fragen, eula.txt direkt schreiben
            # Original: if [[ ! -s "eula.txt" ]]; then
            # Neu: if false; then (EULA wird via eula.txt akzeptiert)
            content = content.replace(
                'if [[ ! -s "eula.txt" ]]; then',
                '# EULA-Check gepatcht fuer systemd (kein interaktives Terminal)\n'
                '  echo "eula=true" > eula.txt\n'
                '  if false; then',
            )

            if content != original:
                await self._sudo_write_file(start_sh, content)
                logger.info("start.sh EULA-Check gepatcht (automatische Akzeptanz)")
            else:
                logger.debug("start.sh EULA-Check bereits gepatcht oder nicht gefunden")

        except Exception as e:
            logger.warning(f"start.sh EULA-Patch fehlgeschlagen: {e}")

    async def _patch_variables_txt(self) -> None:
        """Passt variables.txt für nicht-interaktiven Server-Betrieb an."""
        variables_file = self.server_path / "variables.txt"
        if not variables_file.exists():
            return

        try:
            content = variables_file.read_text()
            original = content

            # WAIT_FOR_USER_INPUT=true → false (systemd wartet nicht auf Input)
            content = re.sub(
                r'WAIT_FOR_USER_INPUT\s*=\s*true',
                'WAIT_FOR_USER_INPUT=false',
                content,
                flags=re.IGNORECASE,
            )

            # RESTART=true → false (systemd übernimmt Restart)
            content = re.sub(
                r'RESTART\s*=\s*true',
                'RESTART=false',
                content,
                flags=re.IGNORECASE,
            )

            if content != original:
                await self._sudo_write_file(variables_file, content)
                logger.info("variables.txt angepasst (WAIT_FOR_USER_INPUT=false, RESTART=false)")
            else:
                logger.debug("variables.txt bereits korrekt konfiguriert")

        except Exception as e:
            logger.warning(f"variables.txt Patch fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # NeoForge Installer (Legacy-Methode für ältere Server Packs)
    # ------------------------------------------------------------------

    async def install(
        self,
        version: str,
        installer_jar: Optional[Path] = None,
    ) -> Tuple[bool, str]:
        """
        Installiert NeoForge — bevorzugt über start.sh, Fallback auf Installer.

        Neue Packs: ensure_startup_script() erstellt run.sh Wrapper
        Alte Packs: NeoForge Installer generiert run.sh direkt

        Returns:
            (erfolg, nachricht)
        """
        # Neue Methode: start.sh vorhanden → Wrapper erstellen
        start_sh = self.server_path / "start.sh"
        if start_sh.exists():
            logger.info(f"Server Pack nutzt start.sh (ServerPackCreator) — Wrapper-Methode")
            return await self.ensure_startup_script()

        # Legacy-Methode: NeoForge Installer
        logger.info(f"NeoForge {version} Installation via Installer gestartet...")

        if not installer_jar or not installer_jar.exists():
            for jar in self.server_path.glob("*neoforge*installer*.jar"):
                if version in jar.name:
                    installer_jar = jar
                    break

        if not installer_jar or not installer_jar.exists():
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
                        with open(installer_jar, "wb") as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                f.write(chunk)
                        size = installer_jar.stat().st_size
                        logger.info(f"Installer heruntergeladen: {size} Bytes")
            except Exception as e:
                return False, f"Installer-Download fehlgeschlagen: {e}"

        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-u", self.server_user,
                "java", "-jar", str(installer_jar), "--installServer",
                cwd=str(self.server_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            err = stderr.decode("utf-8", errors="ignore")

            if proc.returncode != 0:
                return False, f"NeoForge-Installer Fehler (Code {proc.returncode}): {err[:300]}"

            run_sh = self.server_path / "run.sh"
            if run_sh.exists():
                logger.info(f"NeoForge {version} erfolgreich installiert")
                return True, f"NeoForge {version} erfolgreich installiert"
            else:
                logger.warning("run.sh nicht gefunden nach Installation")
                return False, "run.sh wurde nicht generiert"

        except asyncio.TimeoutError:
            return False, "NeoForge-Installer Timeout (>5 Minuten)"
        except Exception as e:
            return False, f"NeoForge-Installer Fehler: {e}"
