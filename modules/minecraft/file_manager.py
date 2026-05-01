"""
FileManager — Streaming Download, ZIP-Extraktion, Hash-Verifikation, Atomic Swap

Stellt die Kernfunktionen für das Auto-Update-System bereit:
- Große Dateien (>500 MB) via Streaming herunterladen
- ZIP-Archive entpacken (nicht-blockierend via asyncio)
- SHA1/MD5-Hash-Verifikation nach Download
- Atomic Swap: Staging → Produktion ohne halbfertigen Zustand
- Disk-Space Pre-Flight Check

Alle Operationen nutzen ein Staging-Verzeichnis und sudo für die
Berechtigungsgrenze botuser→minecraft.
"""

import asyncio
import hashlib
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, Callable, Awaitable

import aiohttp

from utils.logger import get_logger

logger = get_logger("minecraft.file_manager")

# Standard-Staging-Verzeichnis (muss von botuser beschreibbar sein)
DEFAULT_STAGING_DIR = Path("/home/minecraft/.update_staging")

# Chunk-Größe für Streaming-Download (8 KB)
DOWNLOAD_CHUNK_SIZE = 8192

# Download-Timeout: 60 Minuten für große Dateien
DOWNLOAD_TIMEOUT_SEC = 3600

# Minimaler freier Speicherplatz (1.2 GB)
MIN_FREE_SPACE_BYTES = 1_200_000_000


class FileManagerError(Exception):
    """Fehler bei Dateioperationen mit Stage-Information."""

    def __init__(self, stage: str, detail: str):
        self.stage = stage
        self.detail = detail
        super().__init__(f"FileManager-Fehler bei {stage}: {detail}")


class FileManager:
    """
    Verwaltet Dateioperationen für das Auto-Update-System.

    Alle Operationen sind asynchron und Thread-safe.
    Nutzt ein Staging-Verzeichnis für sichere Updates.
    """

    def __init__(self, staging_dir: Path = DEFAULT_STAGING_DIR):
        self.staging_dir = staging_dir

    # ------------------------------------------------------------------
    # Disk-Space Pre-Flight
    # ------------------------------------------------------------------

    async def check_disk_space(self, required_bytes: int) -> bool:
        """
        Prüft ob genug Speicherplatz für Download + Extraktion verfügbar ist.

        Args:
            required_bytes: Erwartete Dateigröße in Bytes

        Returns:
            True wenn genug Platz vorhanden

        Raises:
            FileManagerError wenn zu wenig Platz
        """
        import psutil

        # Faktor 2.2: ZIP + entpackter Inhalt + Overhead
        needed = max(int(required_bytes * 2.2), MIN_FREE_SPACE_BYTES)
        disk = psutil.disk_usage(str(self.staging_dir.parent))

        if disk.free < needed:
            free_mb = disk.free / (1024 * 1024)
            needed_mb = needed / (1024 * 1024)
            raise FileManagerError(
                "disk_space",
                f"Zu wenig Speicherplatz: {free_mb:.0f} MB frei, "
                f"benötigt {needed_mb:.0f} MB"
            )

        free_mb = disk.free / (1024 * 1024)
        logger.info(f"Speicherplatz OK: {free_mb:.0f} MB frei")
        return True

    # ------------------------------------------------------------------
    # Staging-Verzeichnis
    # ------------------------------------------------------------------

    async def create_staging(self) -> Path:
        """
        Erstellt ein eindeutiges Staging-Unterverzeichnis.

        Returns:
            Pfad zum neuen Staging-Verzeichnis
        """
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(datetime.now().timestamp())
        update_dir = self.staging_dir / f"update_{timestamp}"
        update_dir.mkdir(exist_ok=True)

        logger.debug(f"Staging-Verzeichnis erstellt: {update_dir}")
        return update_dir

    async def cleanup_staging(self, staging_path: Path) -> None:
        """Räumt ein Staging-Verzeichnis auf."""
        try:
            if staging_path.exists() and staging_path.is_dir():
                await asyncio.to_thread(shutil.rmtree, staging_path, True)
                logger.debug(f"Staging aufgeräumt: {staging_path}")
        except Exception as e:
            logger.warning(f"Staging-Cleanup fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Streaming Download
    # ------------------------------------------------------------------

    async def download_file(
        self,
        url: str,
        dest_path: Path,
        expected_size: int = 0,
        expected_sha1: Optional[str] = None,
        expected_md5: Optional[str] = None,
        progress_callback: Optional[Callable[[float, float, float], Awaitable]] = None,
        max_retries: int = 3,
    ) -> Tuple[Path, Dict[str, Any]]:
        """
        Lädt eine Datei via Streaming herunter mit Hash-Verifikation.

        Args:
            url: Download-URL
            dest_path: Zieldatei
            expected_size: Erwartete Größe in Bytes (0 = unbekannt)
            expected_sha1: SHA1-Hash für Verifikation (optional)
            expected_md5: MD5-Hash für Verifikation (optional)
            progress_callback: async fn(prozent, heruntergeladen_mb, gesamt_mb)
            max_retries: Maximale Wiederholungsversuche bei Hash-Mismatch

        Returns:
            (Dateipfad, Metadaten-Dict)

        Raises:
            FileManagerError bei Fehlern
        """
        for attempt in range(1, max_retries + 1):
            try:
                metadata = await self._download_once(
                    url, dest_path, expected_size, progress_callback
                )

                # Hash-Verifikation gegen Streaming-Hash (BUG-2: keine doppelte Berechnung)
                if expected_sha1 or expected_md5:
                    hash_ok = True
                    if expected_sha1 and metadata["sha1"].lower() != expected_sha1.lower():
                        logger.warning(
                            f"SHA1-Mismatch: erwartet={expected_sha1}, "
                            f"tatsächlich={metadata['sha1']}"
                        )
                        hash_ok = False
                    elif expected_md5 and metadata["md5"].lower() != expected_md5.lower():
                        logger.warning(
                            f"MD5-Mismatch: erwartet={expected_md5}, "
                            f"tatsaechlich={metadata['md5']}"
                        )
                        hash_ok = False
                    if not hash_ok:
                        if attempt < max_retries:
                            logger.warning(
                                f"Hash-Mismatch (Versuch {attempt}/{max_retries}) "
                                f"— Download wird wiederholt"
                            )
                            dest_path.unlink(missing_ok=True)
                            continue
                        else:
                            dest_path.unlink(missing_ok=True)
                            raise FileManagerError(
                                "download_hash",
                                f"Hash-Verifikation nach {max_retries} Versuchen "
                                f"fehlgeschlagen"
                            )
                    metadata["hash_verified"] = True

                return dest_path, metadata

            except FileManagerError:
                raise
            except Exception as e:
                dest_path.unlink(missing_ok=True)
                if attempt < max_retries:
                    wait = attempt * 10
                    logger.warning(
                        f"Download-Fehler (Versuch {attempt}/{max_retries}): {e} "
                        f"— Warte {wait}s"
                    )
                    await asyncio.sleep(wait)
                else:
                    raise FileManagerError("download", str(e))

        # Sollte nicht erreicht werden
        raise FileManagerError("download", "Unerwarteter Zustand nach Retries")

    async def _download_once(
        self,
        url: str,
        dest_path: Path,
        expected_size: int,
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Einzelner Download-Versuch."""
        logger.info(f"Download gestartet: {url}")
        start_time = datetime.now()
        downloaded = 0

        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SEC)
        headers = {
            "User-Agent": "DiscordBot/4.0.0 (GameServer-Management Auto-Updater)",
        }

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    raise FileManagerError(
                        "download_http",
                        f"HTTP {resp.status} von {url}"
                    )

                total_size = int(resp.headers.get("content-length", expected_size))
                total_mb = total_size / (1024 * 1024) if total_size else 0

                # SHA1 + MD5 gleichzeitig berechnen während des Downloads
                # (nicht-Security: NeoForge-Installer Integritäts-Checks vs. Mojang-Manifests)
                sha1 = hashlib.sha1(usedforsecurity=False)
                md5 = hashlib.md5(usedforsecurity=False)

                dest_path.parent.mkdir(parents=True, exist_ok=True)
                last_progress = -1

                with open(dest_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
                        f.write(chunk)
                        sha1.update(chunk)
                        md5.update(chunk)
                        downloaded += len(chunk)

                        # Progress alle 5%
                        if total_size > 0 and progress_callback:
                            pct = int((downloaded / total_size) * 100)
                            if pct >= last_progress + 5:
                                last_progress = pct
                                dl_mb = downloaded / (1024 * 1024)
                                try:
                                    await progress_callback(pct, dl_mb, total_mb)
                                except Exception as e:
                                    logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

        actual_size = dest_path.stat().st_size
        elapsed = (datetime.now() - start_time).total_seconds()
        speed = (actual_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0

        # Größenprüfung
        if total_size > 0 and actual_size != total_size:
            dest_path.unlink(missing_ok=True)
            raise FileManagerError(
                "download_size",
                f"Größe stimmt nicht: {actual_size} Bytes heruntergeladen, "
                f"{total_size} Bytes erwartet"
            )

        metadata = {
            "url": url,
            "size_bytes": actual_size,
            "size_mb": round(actual_size / (1024 * 1024), 1),
            "duration_sec": round(elapsed, 1),
            "speed_mbps": round(speed, 1),
            "sha1": sha1.hexdigest(),
            "md5": md5.hexdigest(),
            "downloaded_at": datetime.now().isoformat(),
        }

        logger.info(
            f"Download abgeschlossen: {metadata['size_mb']} MB "
            f"in {elapsed:.0f}s ({speed:.1f} MB/s)"
        )
        return metadata

    # ------------------------------------------------------------------
    # Hash-Verifikation
    # ------------------------------------------------------------------

    async def _verify_hash(
        self,
        file_path: Path,
        expected_sha1: Optional[str] = None,
        expected_md5: Optional[str] = None,
    ) -> bool:
        """
        Verifiziert SHA1 und/oder MD5 einer Datei.

        Returns:
            True wenn alle angegebenen Hashes übereinstimmen
        """
        loop = asyncio.get_running_loop()

        def _compute():
            # nicht-Security: Integritaets-Verifikation vs. Mojang/NeoForge-Manifests
            sha1 = hashlib.sha1(usedforsecurity=False)
            md5 = hashlib.md5(usedforsecurity=False)
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    sha1.update(chunk)
                    md5.update(chunk)
            return sha1.hexdigest(), md5.hexdigest()

        actual_sha1, actual_md5 = await loop.run_in_executor(None, _compute)

        if expected_sha1 and actual_sha1.lower() != expected_sha1.lower():
            logger.warning(
                f"SHA1-Mismatch: erwartet={expected_sha1}, "
                f"tatsächlich={actual_sha1}"
            )
            return False

        if expected_md5 and actual_md5.lower() != expected_md5.lower():
            logger.warning(
                f"MD5-Mismatch: erwartet={expected_md5}, "
                f"tatsächlich={actual_md5}"
            )
            return False

        logger.info("Hash-Verifikation erfolgreich")
        return True

    # ------------------------------------------------------------------
    # ZIP-Extraktion
    # ------------------------------------------------------------------

    async def extract_zip(
        self,
        zip_path: Path,
        dest_dir: Path,
        progress_callback: Optional[Callable[[float, int, int], Awaitable]] = None,
    ) -> Dict[str, Any]:
        """
        Entpackt eine ZIP-Datei asynchron (via Thread-Executor).

        Args:
            zip_path: Pfad zur ZIP-Datei
            dest_dir: Zielverzeichnis
            progress_callback: async fn(prozent, entpackt, gesamt)

        Returns:
            Metadaten-Dict

        Raises:
            FileManagerError bei Fehlern
        """
        logger.info(f"ZIP-Extraktion: {zip_path.name} → {dest_dir}")

        # Zielverzeichnis via sudo erstellen (ProtectHome=read-only)
        dest_dir.mkdir(parents=True, exist_ok=True)

        loop = asyncio.get_running_loop()
        start_time = datetime.now()

        def _extract_sync():
            """Synchrone Extraktion im Thread-Executor."""
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Integritätsprüfung
                bad = zf.testzip()
                if bad:
                    raise zipfile.BadZipFile(
                        f"Korrupte Datei im Archiv: {bad}"
                    )

                entries = zf.infolist()
                total = len(entries)
                # dest_dir wurde bereits vor _extract_sync erstellt

                for i, entry in enumerate(entries, 1):
                    zf.extract(entry, dest_dir)

                return total

        try:
            total_files = await loop.run_in_executor(None, _extract_sync)
            elapsed = (datetime.now() - start_time).total_seconds()

            metadata = {
                "zip_file": str(zip_path),
                "dest_dir": str(dest_dir),
                "total_files": total_files,
                "extract_time_sec": round(elapsed, 1),
                "extracted_at": datetime.now().isoformat(),
            }

            logger.info(
                f"Extraktion abgeschlossen: {total_files} Dateien "
                f"in {elapsed:.1f}s"
            )
            return metadata

        except zipfile.BadZipFile as e:
            raise FileManagerError("extract_corrupt", str(e))
        except Exception as e:
            raise FileManagerError("extract", str(e))

    # ------------------------------------------------------------------
    # Atomic Swap
    # ------------------------------------------------------------------

    async def atomic_swap(
        self,
        source_dir: Path,
        target_dir: Path,
        rollback_dir: Path,
        server_user: str = "minecraft",
    ) -> Dict[str, Any]:
        """
        Atomarer Austausch: target → rollback, source → target.

        Nutzt sudo für Berechtigungen zwischen botuser und minecraft.

        Args:
            source_dir: Neue Dateien (aus Staging)
            target_dir: Aktueller Server-Ordner
            rollback_dir: Wohin der alte Ordner verschoben wird
            server_user: Benutzer für chown

        Returns:
            Metadaten-Dict

        Raises:
            FileManagerError bei Fehlern
        """
        logger.info(f"Atomic Swap: {target_dir} → Rollback, {source_dir} → Produktion")

        try:
            # Schritt 1: Alten Ordner als Rollback sichern
            rollback_dir.parent.mkdir(parents=True, exist_ok=True)
            if rollback_dir.exists():
                await asyncio.to_thread(shutil.rmtree, rollback_dir)

            # Für Operationen innerhalb des minecraft-Verzeichnisses: sudo verwenden
            proc = await asyncio.create_subprocess_exec(
                "sudo", "mv", str(target_dir), str(rollback_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode != 0:
                raise FileManagerError(
                    "swap_backup",
                    f"Backup fehlgeschlagen: {stderr.decode().strip()}"
                )
            logger.info(f"Rollback gesichert: {rollback_dir}")

            # Schritt 2: Neue Dateien an Ziel verschieben
            proc = await asyncio.create_subprocess_exec(
                "sudo", "mv", str(source_dir), str(target_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode != 0:
                # Rollback: Alten Ordner zurück
                logger.error("Swap fehlgeschlagen — stelle Rollback wieder her")
                await asyncio.create_subprocess_exec(
                    "sudo", "mv", str(rollback_dir), str(target_dir),
                )
                raise FileManagerError(
                    "swap_install",
                    f"Installation fehlgeschlagen: {stderr.decode().strip()}"
                )
            logger.info(f"Neue Version installiert: {target_dir}")

            # Schritt 3: Berechtigungen setzen
            proc = await asyncio.create_subprocess_exec(
                "sudo", "chown", "-R",
                f"{server_user}:{server_user}", str(target_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                logger.warning("chown fehlgeschlagen — Berechtigungen prüfen!")

            return {
                "source": str(source_dir),
                "target": str(target_dir),
                "rollback": str(rollback_dir),
                "swapped_at": datetime.now().isoformat(),
            }

        except FileManagerError:
            raise
        except asyncio.TimeoutError:
            raise FileManagerError("swap_timeout", "Dateioperation Timeout")
        except Exception as e:
            raise FileManagerError("swap", str(e))

    # ------------------------------------------------------------------
    # Custom-Dateien sichern/wiederherstellen
    # ------------------------------------------------------------------

    async def backup_custom_files(
        self,
        server_dir: Path,
        file_list: list[str],
    ) -> Dict[str, Path]:
        """
        Sichert Custom-Dateien vor dem Update.

        Args:
            server_dir: Server-Verzeichnis
            file_list: Liste der Dateinamen (z.B. ["server.properties", "ops.json"])

        Returns:
            Dict mit {dateiname: temp_pfad}
        """
        backed_up = {}
        temp_dir = self.staging_dir / "custom_backup"
        temp_dir.mkdir(parents=True, exist_ok=True)

        for filename in file_list:
            src = server_dir / filename
            if src.exists():
                dest = temp_dir / filename
                try:
                    # sudo cat für Leserechte
                    proc = await asyncio.create_subprocess_exec(
                        "sudo", "cp", str(src), str(dest),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=10)
                    if proc.returncode == 0:
                        backed_up[filename] = dest
                        logger.debug(f"Custom-Datei gesichert: {filename}")
                except Exception as e:
                    logger.warning(f"Custom-Datei {filename} konnte nicht gesichert werden: {e}")

        logger.info(f"{len(backed_up)} Custom-Dateien gesichert")
        return backed_up

    async def restore_custom_files(
        self,
        target_dir: Path,
        backed_up: Dict[str, Path],
        server_user: str = "minecraft",
    ) -> int:
        """
        Stellt Custom-Dateien nach dem Update wieder her.

        Args:
            target_dir: Zielverzeichnis (neuer Server-Ordner)
            backed_up: Dict von backup_custom_files()
            server_user: Benutzer für chown

        Returns:
            Anzahl wiederhergestellter Dateien
        """
        restored = 0
        for filename, temp_path in backed_up.items():
            dest = target_dir / filename
            try:
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "cp", str(temp_path), str(dest),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=10)
                if proc.returncode == 0:
                    # Berechtigungen korrigieren
                    await asyncio.create_subprocess_exec(
                        "sudo", "chown",
                        f"{server_user}:{server_user}", str(dest),
                    )
                    restored += 1
                    logger.debug(f"Custom-Datei wiederhergestellt: {filename}")
            except Exception as e:
                logger.warning(
                    f"Custom-Datei {filename} konnte nicht "
                    f"wiederhergestellt werden: {e}"
                )

        logger.info(f"{restored}/{len(backed_up)} Custom-Dateien wiederhergestellt")
        return restored

    # ------------------------------------------------------------------
    # Rollback-Rotation
    # ------------------------------------------------------------------

    async def rotate_rollbacks(
        self,
        rollback_base_dir: Path,
        prefix: str,
        keep_count: int = 2,
    ) -> int:
        """
        Bereinigt alte Rollback-Verzeichnisse (versionsbasiert).

        Behält die neuesten `keep_count` Versionen und löscht den Rest.

        Args:
            rollback_base_dir: Verzeichnis mit Rollback-Ordnern
            prefix: Prefix der Rollback-Ordner (z.B. "mods_rollback_")
            keep_count: Anzahl zu behaltender Versionen

        Returns:
            Anzahl gelöschter Ordner
        """
        if not rollback_base_dir.exists():
            return 0

        rollbacks = sorted(
            [d for d in rollback_base_dir.iterdir()
             if d.is_dir() and d.name.startswith(prefix)],
            key=lambda d: d.stat().st_mtime,
            reverse=True,  # Neueste zuerst
        )

        deleted = 0
        for old_dir in rollbacks[keep_count:]:
            try:
                await asyncio.to_thread(shutil.rmtree, old_dir, True)
                deleted += 1
                logger.info(f"Alter Rollback gelöscht: {old_dir.name}")
            except Exception as e:
                logger.warning(f"Rollback-Löschung fehlgeschlagen: {old_dir.name}: {e}")

        if deleted > 0:
            logger.info(
                f"Rollback-Rotation: {deleted} gelöscht, "
                f"{min(len(rollbacks), keep_count)} behalten"
            )
        return deleted
