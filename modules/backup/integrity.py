"""
F33: Backup Integrity — Prüft die Integrität von Backup-Archiven.

Berechnet SHA256-Checksummen, testet tar.gz-Archive und prüft
die Dateigröße gegen vorherige Backups auf Plausibilität.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Optional

from utils.logger import get_logger
from utils.config import get_config, get_env, PROJECT_ROOT

logger = get_logger("backup.integrity")

# Puffer-Größe für Checksum-Berechnung (64 KB)
HASH_BUFFER_SIZE: int = 65536

# Größen-Schwellenwerte (Prozent der Referenz-Größe)
SIZE_MIN_PERCENT: float = 50.0   # Warnung wenn <50% der Referenz
SIZE_MAX_PERCENT: float = 200.0  # Warnung wenn >200% der Referenz

# Timeout fuer tar-Archiv-Test (Sekunden)
TAR_TEST_TIMEOUT: float = 120.0


class BackupIntegrity:
    """
    Prüft die Integrität von Backup-Archiven.

    Führt drei Prüfungen durch:
    1. SHA256-Checksum berechnen
    2. Archiv-Struktur testen (tar -tzf)
    3. Größen-Plausibilität gegen Referenz-Größe prüfen

    Die Referenz-Größe kann manuell gesetzt oder automatisch
    aus dem vorherigen Backup ermittelt werden.
    """

    def __init__(
        self,
        reference_size: Optional[int] = None,
        size_min_percent: float = SIZE_MIN_PERCENT,
        size_max_percent: float = SIZE_MAX_PERCENT,
    ) -> None:
        """
        Args:
            reference_size: Referenz-Größe in Bytes für den Größen-Check
                           (None = kein Größen-Vergleich)
            size_min_percent: Minimale Größe in % der Referenz (Standard: 50%)
            size_max_percent: Maximale Größe in % der Referenz (Standard: 200%)
        """
        self._reference_size: Optional[int] = reference_size
        self._size_min_percent = size_min_percent
        self._size_max_percent = size_max_percent

        logger.info(
            "BackupIntegrity initialisiert (Referenz: %s, Bereich: %.0f%%–%.0f%%)",
            f"{reference_size} Bytes" if reference_size else "keine",
            size_min_percent, size_max_percent
        )

    @property
    def reference_size(self) -> Optional[int]:
        """Aktuelle Referenz-Größe für den Größen-Vergleich."""
        return self._reference_size

    @reference_size.setter
    def reference_size(self, value: Optional[int]) -> None:
        """Setzt die Referenz-Größe (z.B. Größe des vorherigen Backups)."""
        self._reference_size = value
        if value is not None:
            logger.debug("Referenz-Größe aktualisiert: %d Bytes", value)

    @staticmethod
    def _compute_sha256(filepath: Path) -> str:
        """
        Berechnet den SHA256-Hash einer Datei (blockierend).

        Wird via asyncio.to_thread aufgerufen.

        Args:
            filepath: Pfad zur Datei

        Returns:
            SHA256-Hex-String
        """
        sha256 = hashlib.sha256()
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(HASH_BUFFER_SIZE)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256.hexdigest()

    async def compute_checksum(self, filepath: Path) -> str:
        """
        Berechnet die SHA256-Checksum einer Datei (async).

        Args:
            filepath: Pfad zur Backup-Datei

        Returns:
            SHA256-Hex-String

        Raises:
            FileNotFoundError: Wenn die Datei nicht existiert
            OSError: Bei Lesefehler
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"Datei nicht gefunden: {filepath}")

        checksum = await asyncio.to_thread(self._compute_sha256, filepath)
        logger.debug("Checksum fuer %s: %s", filepath.name, checksum[:16] + "...")
        return checksum

    async def test_archive(self, filepath: Path) -> tuple[bool, str]:
        """
        Testet ein tar.gz-Archiv auf Integrität mittels tar -tzf.

        Args:
            filepath: Pfad zur Archiv-Datei

        Returns:
            Tuple (ok, detail):
                - ok (bool): True wenn Archiv intakt
                - detail (str): Beschreibung des Ergebnisses
        """
        filepath = Path(filepath)
        if not filepath.exists():
            return False, f"Datei nicht gefunden: {filepath}"

        try:
            proc = await asyncio.create_subprocess_exec(
                "tar", "-tzf", str(filepath),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=TAR_TEST_TIMEOUT
            )

            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.warning("Archiv-Test fehlgeschlagen für %s: %s", filepath.name, error_msg)
                return False, f"Archiv beschädigt: {error_msg[:200]}"

            # Einträge zählen
            output = stdout.decode("utf-8", errors="replace")
            entries = [line for line in output.strip().split("\n") if line.strip()]
            entry_count = len(entries)

            if entry_count == 0:
                logger.warning("Archiv %s ist leer", filepath.name)
                return False, "Archiv ist leer (0 Einträge)"

            logger.debug("Archiv %s OK: %d Einträge", filepath.name, entry_count)
            return True, f"Archiv intakt: {entry_count} Einträge"

        except asyncio.TimeoutError:
            logger.error("Archiv-Test Timeout fuer %s nach %.0fs", filepath.name, TAR_TEST_TIMEOUT)
            return False, f"Timeout nach {TAR_TEST_TIMEOUT:.0f}s"
        except FileNotFoundError:
            logger.error("tar nicht gefunden — ist tar installiert?")
            return False, "tar-Befehl nicht gefunden"
        except OSError as e:
            logger.error("Archiv-Test Fehler für %s: %s", filepath.name, e)
            return False, f"Ausführungsfehler: {e}"

    def check_size(self, size_bytes: int) -> tuple[bool, str]:
        """
        Prüft ob die Dateigröße plausibel ist im Vergleich zur Referenz.

        Args:
            size_bytes: Größe der zu prüfenden Datei in Bytes

        Returns:
            Tuple (ok, detail):
                - ok (bool): True wenn Größe plausibel
                - detail (str): Beschreibung des Ergebnisses
        """
        if size_bytes <= 0:
            return False, "Datei ist leer (0 Bytes)"

        if self._reference_size is None or self._reference_size <= 0:
            return True, f"Größe: {size_bytes} Bytes (kein Referenzwert vorhanden)"

        # Prozentualer Vergleich
        percent = (size_bytes / self._reference_size) * 100.0

        if percent < self._size_min_percent:
            detail = (
                f"Größe verdächtig klein: {size_bytes} Bytes "
                f"({percent:.1f}% der Referenz {self._reference_size} Bytes, "
                f"Minimum: {self._size_min_percent:.0f}%)"
            )
            logger.warning(detail)
            return False, detail

        if percent > self._size_max_percent:
            detail = (
                f"Größe verdächtig groß: {size_bytes} Bytes "
                f"({percent:.1f}% der Referenz {self._reference_size} Bytes, "
                f"Maximum: {self._size_max_percent:.0f}%)"
            )
            logger.warning(detail)
            return False, detail

        detail = (
            f"Größe OK: {size_bytes} Bytes "
            f"({percent:.1f}% der Referenz {self._reference_size} Bytes)"
        )
        logger.debug(detail)
        return True, detail

    async def verify_backup(self, filepath: Path) -> dict:
        """
        Führt eine vollständige Integritätsprüfung eines Backups durch.

        Prüft SHA256-Checksum, Archiv-Integrität und Größen-Plausibilität.

        Args:
            filepath: Pfad zur Backup-Datei (tar.gz)

        Returns:
            Dict mit Keys:
                - path (str): Absoluter Pfad zur Datei
                - checksum (str|None): SHA256-Hex-String
                - size_bytes (int): Dateigröße in Bytes
                - archive_ok (bool): True wenn Archiv intakt
                - size_ok (bool): True wenn Größe plausibel
                - details (dict): Detaillierte Ergebnisse der Einzelprüfungen
                    - checksum_detail (str): Checksum oder Fehlermeldung
                    - archive_detail (str): Archiv-Test-Ergebnis
                    - size_detail (str): Größen-Prüfungsergebnis
                - ok (bool): True wenn alle Prüfungen bestanden
                - error (str|None): Fehlermeldung bei grundlegendem Fehler
        """
        filepath = Path(filepath).resolve()

        result: dict = {
            "path": str(filepath),
            "checksum": None,
            "size_bytes": 0,
            "archive_ok": False,
            "size_ok": False,
            "details": {
                "checksum_detail": "",
                "archive_detail": "",
                "size_detail": "",
            },
            "ok": False,
            "error": None,
        }

        # Existenz prüfen
        if not filepath.exists():
            result["error"] = f"Datei nicht gefunden: {filepath}"
            result["details"]["checksum_detail"] = "Übersprungen — Datei fehlt"
            result["details"]["archive_detail"] = "Übersprungen — Datei fehlt"
            result["details"]["size_detail"] = "Übersprungen — Datei fehlt"
            logger.error("Integritätsprüfung: %s", result["error"])
            return result

        if not filepath.is_file():
            result["error"] = f"Kein reguläres File: {filepath}"
            logger.error("Integritätsprüfung: %s", result["error"])
            return result

        # Dateigröße ermitteln
        try:
            result["size_bytes"] = filepath.stat().st_size
        except OSError as e:
            result["error"] = f"Kann Dateigröße nicht lesen: {e}"
            logger.error("Integritätsprüfung: %s", result["error"])
            return result

        # 1. SHA256-Checksum berechnen
        try:
            checksum = await self.compute_checksum(filepath)
            result["checksum"] = checksum
            result["details"]["checksum_detail"] = f"SHA256: {checksum}"
        except (OSError, PermissionError) as e:
            result["details"]["checksum_detail"] = f"Checksum-Fehler: {e}"
            logger.error("Checksum-Berechnung fehlgeschlagen für %s: %s", filepath.name, e)

        # 2. Archiv-Test (tar -tzf)
        archive_ok, archive_detail = await self.test_archive(filepath)
        result["archive_ok"] = archive_ok
        result["details"]["archive_detail"] = archive_detail

        # 3. Größen-Plausibilität
        size_ok, size_detail = self.check_size(result["size_bytes"])
        result["size_ok"] = size_ok
        result["details"]["size_detail"] = size_detail

        # Gesamtergebnis: OK nur wenn Archiv und Groesse in Ordnung
        result["ok"] = archive_ok and size_ok

        # Referenz-Größe für nächsten Vergleich aktualisieren
        # (nur wenn Backup als OK befunden wurde)
        if result["ok"] and result["size_bytes"] > 0:
            self._reference_size = result["size_bytes"]

        # Ergebnis loggen
        if result["ok"]:
            logger.info(
                "Backup-Integrität OK: %s (%d Bytes, SHA256: %s)",
                filepath.name, result["size_bytes"],
                result["checksum"][:16] + "..." if result["checksum"] else "N/A"
            )
        else:
            logger.warning(
                "Backup-Integrität FEHLGESCHLAGEN: %s — Archiv: %s, Größe: %s",
                filepath.name, archive_ok, size_ok
            )

        return result
