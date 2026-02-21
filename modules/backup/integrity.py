"""
F33: Backup Integrity — Prueft die Integritaet von Backup-Archiven.

Berechnet SHA256-Checksummen, testet tar.gz-Archive und prueft
die Dateigrösse gegen vorherige Backups auf Plausibilitaet.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Optional

from utils.logger import get_logger
from utils.config import get_config, get_env, PROJECT_ROOT

logger = get_logger("backup.integrity")

# Puffer-Groesse fuer Checksum-Berechnung (64 KB)
HASH_BUFFER_SIZE: int = 65536

# Groessen-Schwellenwerte (Prozent der Referenz-Groesse)
SIZE_MIN_PERCENT: float = 50.0   # Warnung wenn <50% der Referenz
SIZE_MAX_PERCENT: float = 200.0  # Warnung wenn >200% der Referenz

# Timeout fuer tar-Archiv-Test (Sekunden)
TAR_TEST_TIMEOUT: float = 120.0


class BackupIntegrity:
    """
    Prueft die Integritaet von Backup-Archiven.

    Fuehrt drei Pruefungen durch:
    1. SHA256-Checksum berechnen
    2. Archiv-Struktur testen (tar -tzf)
    3. Groessen-Plausibilitaet gegen Referenz-Groesse pruefen

    Die Referenz-Groesse kann manuell gesetzt oder automatisch
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
            reference_size: Referenz-Groesse in Bytes fuer den Groessen-Check
                           (None = kein Groessen-Vergleich)
            size_min_percent: Minimale Groesse in % der Referenz (Standard: 50%)
            size_max_percent: Maximale Groesse in % der Referenz (Standard: 200%)
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
        """Aktuelle Referenz-Groesse fuer den Groessen-Vergleich."""
        return self._reference_size

    @reference_size.setter
    def reference_size(self, value: Optional[int]) -> None:
        """Setzt die Referenz-Groesse (z.B. Groesse des vorherigen Backups)."""
        self._reference_size = value
        if value is not None:
            logger.debug("Referenz-Groesse aktualisiert: %d Bytes", value)

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
        Testet ein tar.gz-Archiv auf Integritaet mittels tar -tzf.

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
                logger.warning("Archiv-Test fehlgeschlagen fuer %s: %s", filepath.name, error_msg)
                return False, f"Archiv beschaedigt: {error_msg[:200]}"

            # Eintraege zaehlen
            output = stdout.decode("utf-8", errors="replace")
            entries = [line for line in output.strip().split("\n") if line.strip()]
            entry_count = len(entries)

            if entry_count == 0:
                logger.warning("Archiv %s ist leer", filepath.name)
                return False, "Archiv ist leer (0 Eintraege)"

            logger.debug("Archiv %s OK: %d Eintraege", filepath.name, entry_count)
            return True, f"Archiv intakt: {entry_count} Eintraege"

        except asyncio.TimeoutError:
            logger.error("Archiv-Test Timeout fuer %s nach %.0fs", filepath.name, TAR_TEST_TIMEOUT)
            return False, f"Timeout nach {TAR_TEST_TIMEOUT:.0f}s"
        except FileNotFoundError:
            logger.error("tar nicht gefunden — ist tar installiert?")
            return False, "tar-Befehl nicht gefunden"
        except OSError as e:
            logger.error("Archiv-Test Fehler fuer %s: %s", filepath.name, e)
            return False, f"Ausfuehrungsfehler: {e}"

    def check_size(self, size_bytes: int) -> tuple[bool, str]:
        """
        Prueft ob die Dateigrösse plausibel ist im Vergleich zur Referenz.

        Args:
            size_bytes: Groesse der zu pruefenden Datei in Bytes

        Returns:
            Tuple (ok, detail):
                - ok (bool): True wenn Groesse plausibel
                - detail (str): Beschreibung des Ergebnisses
        """
        if size_bytes <= 0:
            return False, "Datei ist leer (0 Bytes)"

        if self._reference_size is None or self._reference_size <= 0:
            return True, f"Groesse: {size_bytes} Bytes (kein Referenzwert vorhanden)"

        # Prozentualer Vergleich
        percent = (size_bytes / self._reference_size) * 100.0

        if percent < self._size_min_percent:
            detail = (
                f"Groesse verdaechtig klein: {size_bytes} Bytes "
                f"({percent:.1f}% der Referenz {self._reference_size} Bytes, "
                f"Minimum: {self._size_min_percent:.0f}%)"
            )
            logger.warning(detail)
            return False, detail

        if percent > self._size_max_percent:
            detail = (
                f"Groesse verdaechtig gross: {size_bytes} Bytes "
                f"({percent:.1f}% der Referenz {self._reference_size} Bytes, "
                f"Maximum: {self._size_max_percent:.0f}%)"
            )
            logger.warning(detail)
            return False, detail

        detail = (
            f"Groesse OK: {size_bytes} Bytes "
            f"({percent:.1f}% der Referenz {self._reference_size} Bytes)"
        )
        logger.debug(detail)
        return True, detail

    async def verify_backup(self, filepath: Path) -> dict:
        """
        Fuehrt eine vollstaendige Integritaetspruefung eines Backups durch.

        Prueft SHA256-Checksum, Archiv-Integritaet und Groessen-Plausibilitaet.

        Args:
            filepath: Pfad zur Backup-Datei (tar.gz)

        Returns:
            Dict mit Keys:
                - path (str): Absoluter Pfad zur Datei
                - checksum (str|None): SHA256-Hex-String
                - size_bytes (int): Dateigroesse in Bytes
                - archive_ok (bool): True wenn Archiv intakt
                - size_ok (bool): True wenn Groesse plausibel
                - details (dict): Detaillierte Ergebnisse der Einzelpruefungen
                    - checksum_detail (str): Checksum oder Fehlermeldung
                    - archive_detail (str): Archiv-Test-Ergebnis
                    - size_detail (str): Groessen-Pruefergebnis
                - ok (bool): True wenn alle Pruefungen bestanden
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

        # Existenz pruefen
        if not filepath.exists():
            result["error"] = f"Datei nicht gefunden: {filepath}"
            result["details"]["checksum_detail"] = "Uebersprungen — Datei fehlt"
            result["details"]["archive_detail"] = "Uebersprungen — Datei fehlt"
            result["details"]["size_detail"] = "Uebersprungen — Datei fehlt"
            logger.error("Integritaetspruefung: %s", result["error"])
            return result

        if not filepath.is_file():
            result["error"] = f"Kein regulaeres File: {filepath}"
            logger.error("Integritaetspruefung: %s", result["error"])
            return result

        # Dateigroesse ermitteln
        try:
            result["size_bytes"] = filepath.stat().st_size
        except OSError as e:
            result["error"] = f"Kann Dateigroesse nicht lesen: {e}"
            logger.error("Integritaetspruefung: %s", result["error"])
            return result

        # 1. SHA256-Checksum berechnen
        try:
            checksum = await self.compute_checksum(filepath)
            result["checksum"] = checksum
            result["details"]["checksum_detail"] = f"SHA256: {checksum}"
        except (OSError, PermissionError) as e:
            result["details"]["checksum_detail"] = f"Checksum-Fehler: {e}"
            logger.error("Checksum-Berechnung fehlgeschlagen fuer %s: %s", filepath.name, e)

        # 2. Archiv-Test (tar -tzf)
        archive_ok, archive_detail = await self.test_archive(filepath)
        result["archive_ok"] = archive_ok
        result["details"]["archive_detail"] = archive_detail

        # 3. Groessen-Plausibilitaet
        size_ok, size_detail = self.check_size(result["size_bytes"])
        result["size_ok"] = size_ok
        result["details"]["size_detail"] = size_detail

        # Gesamtergebnis: OK nur wenn Archiv und Groesse in Ordnung
        result["ok"] = archive_ok and size_ok

        # Referenz-Groesse fuer naechsten Vergleich aktualisieren
        # (nur wenn Backup als OK befunden wurde)
        if result["ok"] and result["size_bytes"] > 0:
            self._reference_size = result["size_bytes"]

        # Ergebnis loggen
        if result["ok"]:
            logger.info(
                "Backup-Integritaet OK: %s (%d Bytes, SHA256: %s)",
                filepath.name, result["size_bytes"],
                result["checksum"][:16] + "..." if result["checksum"] else "N/A"
            )
        else:
            logger.warning(
                "Backup-Integritaet FEHLGESCHLAGEN: %s — Archiv: %s, Groesse: %s",
                filepath.name, archive_ok, size_ok
            )

        return result
