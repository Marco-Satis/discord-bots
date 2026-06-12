"""
Config Hot-Reload — Ueberwacht config.json auf Aenderungen und benachrichtigt Bots.

Nutzt mtime-basierte Erkennung (kein watchdog noetig). Registrierte Callbacks
werden bei jeder Aenderung mit dem neuen Config-Dict aufgerufen.

Aenderungen werden optional in der config_history-Tabelle protokolliert.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from utils.logger import get_logger

logger = get_logger("config_reloader")


class ConfigReloader:
    """
    Ueberwacht eine config.json-Datei auf Aenderungen (mtime-basiert)
    und ruft registrierte Callbacks auf, sobald sich die Datei aendert.
    """

    def __init__(self, config_path: Path, check_interval: float = 5.0) -> None:
        """
        Initialisiert den ConfigReloader.

        Args:
            config_path: Pfad zur config.json-Datei.
            check_interval: Pruefintervall in Sekunden (Standard: 5.0).
        """
        self.config_path: Path = Path(config_path)
        self.check_interval: float = check_interval

        # Letzte bekannte Aenderungszeit der Datei
        self._last_mtime: float = 0.0

        # Aktuelle Config im Speicher
        self._current_config: dict = {}

        # Registrierte Callbacks: Name -> async Callable
        self._callbacks: dict[str, Callable] = {}

        # Hintergrund-Task-Referenz
        self._task: Optional[asyncio.Task] = None

        # Steuerungs-Flag fuer die Schleife
        self._running: bool = False

        # Initiale Config laden, falls Datei existiert
        self._load_initial_config()

    # ------------------------------------------------------------------
    # Initialisierung
    # ------------------------------------------------------------------

    def _load_initial_config(self) -> None:
        """Laedt die Config beim Start und speichert die initiale mtime."""
        if not self.config_path.exists():
            logger.warning(
                "Config-Datei nicht gefunden: %s — starte mit leerer Config",
                self.config_path,
            )
            self._current_config = {}
            return

        try:
            self._last_mtime = self.config_path.stat().st_mtime
            self._current_config = json.loads(
                self.config_path.read_text(encoding="utf-8")
            )
            logger.info(
                "Initiale Config geladen: %s (mtime=%.2f)",
                self.config_path,
                self._last_mtime,
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Fehler beim Laden der initialen Config: %s", exc)
            self._current_config = {}

    # ------------------------------------------------------------------
    # Callback-Verwaltung
    # ------------------------------------------------------------------

    def register_callback(self, name: str, callback: Callable) -> None:
        """
        Registriert einen Reload-Callback.

        Der Callback wird bei jeder Config-Aenderung aufgerufen und erhaelt
        das neue Config-Dict als einzigen Parameter.

        Args:
            name: Eindeutiger Name fuer den Callback (z.B. "operator_bot").
            callback: Async-Funktion mit Signatur ``async def cb(new_config: dict)``.
        """
        if name in self._callbacks:
            logger.warning("Callback '%s' wird ueberschrieben", name)
        self._callbacks[name] = callback
        logger.info("Callback registriert: '%s'", name)

    def unregister_callback(self, name: str) -> bool:
        """
        Entfernt einen registrierten Callback.

        Args:
            name: Name des zu entfernenden Callbacks.

        Returns:
            True wenn der Callback existierte und entfernt wurde.
        """
        if name in self._callbacks:
            del self._callbacks[name]
            logger.info("Callback entfernt: '%s'", name)
            return True
        return False

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Startet den Hintergrund-Task, der config.json periodisch prueft.

        Wenn bereits gestartet, wird nichts unternommen.
        """
        if self._running:
            logger.warning("ConfigReloader laeuft bereits — start() ignoriert")
            return

        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info(
            "ConfigReloader gestartet (Intervall: %.1fs, Callbacks: %d)",
            self.check_interval,
            len(self._callbacks),
        )

    async def stop(self) -> None:
        """
        Stoppt den Hintergrund-Task sauber.

        Wartet bis der laufende Pruef-Zyklus abgeschlossen ist.
        """
        if not self._running:
            return

        self._running = False

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("ConfigReloader gestoppt")

    # ------------------------------------------------------------------
    # Interne Pruef-Schleife
    # ------------------------------------------------------------------

    async def _watch_loop(self) -> None:
        """Endlos-Schleife: prueft die mtime alle check_interval Sekunden."""
        while self._running:
            try:
                await self._check_and_reload()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Fehler loggen, aber Schleife nicht abbrechen
                logger.error("Fehler bei Config-Pruefung: %s", exc)

            try:
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                raise

    async def _check_and_reload(self) -> list[str]:
        """
        Prueft ob sich die config.json geaendert hat (mtime-Vergleich).

        Falls ja: laedt die neue Config, ruft alle Callbacks auf und
        protokolliert die Aenderung in der config_history-Tabelle.

        Returns:
            Liste der erfolgreich ausgefuehrten Callback-Namen.
        """
        if not self.config_path.exists():
            return []

        current_mtime = self.config_path.stat().st_mtime
        if current_mtime == self._last_mtime:
            # Keine Aenderung
            return []

        # Config hat sich geaendert!
        logger.info(
            "Config-Aenderung erkannt (mtime: %.2f -> %.2f)",
            self._last_mtime,
            current_mtime,
        )

        self._last_mtime = current_mtime

        # Neue Config laden
        try:
            new_config = json.loads(
                self.config_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Fehler beim Laden der neuen Config: %s", exc)
            return []

        old_config = self._current_config
        self._current_config = new_config

        # Aenderung in config_history protokollieren (optional, Fehler nicht fatal)
        await self._log_to_history(old_config, new_config)

        # Alle registrierten Callbacks ausfuehren
        executed = await self._execute_callbacks(new_config)

        logger.info(
            "Config-Reload abgeschlossen: %d/%d Callbacks erfolgreich",
            len(executed),
            len(self._callbacks),
        )

        return executed

    # ------------------------------------------------------------------
    # Callback-Ausfuehrung
    # ------------------------------------------------------------------

    async def _execute_callbacks(self, new_config: dict) -> list[str]:
        """
        Fuehrt alle registrierten Callbacks aus.

        Args:
            new_config: Das neue Config-Dict.

        Returns:
            Liste der Namen erfolgreich ausgefuehrter Callbacks.
        """
        executed: list[str] = []

        for name, callback in self._callbacks.items():
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(new_config)
                else:
                    # Synchrone Callbacks werden ebenfalls unterstuetzt
                    callback(new_config)
                executed.append(name)
                logger.debug("Callback '%s' erfolgreich ausgefuehrt", name)
            except Exception as exc:
                logger.error(
                    "Callback '%s' fehlgeschlagen: %s", name, exc
                )

        return executed

    # ------------------------------------------------------------------
    # History-Protokollierung
    # ------------------------------------------------------------------

    async def _log_to_history(self, old_config: dict, new_config: dict) -> None:
        """
        Protokolliert die Config-Aenderung in der config_history-Tabelle.

        Vergleicht alte und neue Config und loggt geaenderte Top-Level-Keys.
        Falls das config_history-Modul nicht verfuegbar ist, wird nur geloggt.
        """
        try:
            from modules.config_history import log_config_change

            # Geaenderte Top-Level-Keys ermitteln
            all_keys = set(old_config.keys()) | set(new_config.keys())

            for key in sorted(all_keys):
                old_val = old_config.get(key)
                new_val = new_config.get(key)

                if old_val != new_val:
                    await log_config_change(
                        key=key,
                        old_value=old_val,
                        new_value=new_val,
                        changed_by="config_reloader",
                        source="file_change",
                    )

        except ImportError:
            logger.debug(
                "config_history-Modul nicht verfuegbar — "
                "History-Protokollierung uebersprungen"
            )
        except Exception as exc:
            # History-Fehler sind nicht kritisch
            logger.warning("Config-History Protokollierung fehlgeschlagen: %s", exc)

    # ------------------------------------------------------------------
    # Erzwungener Reload (fuer API-Aufrufe)
    # ------------------------------------------------------------------

    async def force_reload(self) -> list[str]:
        """
        Erzwingt einen sofortigen Reload — unabhaengig von der mtime.

        Laedt die Config neu und fuehrt alle Callbacks aus.
        Wird z.B. vom API-Endpunkt POST /api/config/reload aufgerufen.

        Returns:
            Liste der erfolgreich ausgefuehrten Callback-Namen.
        """
        if not self.config_path.exists():
            logger.error(
                "Config-Datei nicht gefunden: %s — Reload abgebrochen",
                self.config_path,
            )
            return []

        logger.info("Erzwungener Config-Reload gestartet")

        try:
            new_config = json.loads(
                self.config_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Fehler beim Laden der Config: %s", exc)
            return []

        # mtime aktualisieren, damit _check_and_reload nicht nochmal triggert
        self._last_mtime = self.config_path.stat().st_mtime

        old_config = self._current_config
        self._current_config = new_config

        # History protokollieren
        await self._log_to_history(old_config, new_config)

        # Callbacks ausfuehren
        executed = await self._execute_callbacks(new_config)

        logger.info(
            "Erzwungener Reload abgeschlossen: %d/%d Callbacks erfolgreich",
            len(executed),
            len(self._callbacks),
        )

        return executed

    # ------------------------------------------------------------------
    # Config-Zugriff
    # ------------------------------------------------------------------

    def get_config(self) -> dict:
        """
        Gibt die aktuelle Config zurueck (aus dem Speicher).

        Returns:
            Das zuletzt geladene Config-Dict.
        """
        return self._current_config

    @property
    def is_running(self) -> bool:
        """Gibt zurueck ob der Watcher aktiv ist."""
        return self._running

    @property
    def callback_count(self) -> int:
        """Gibt die Anzahl registrierter Callbacks zurueck."""
        return len(self._callbacks)

    @property
    def callback_names(self) -> list[str]:
        """Gibt die Namen aller registrierten Callbacks zurueck."""
        return list(self._callbacks.keys())


# ---------------------------------------------------------------------------
# Globale Singleton-Instanz (optional — kann auch pro Bot erstellt werden)
# ---------------------------------------------------------------------------

_global_reloader: Optional[ConfigReloader] = None


def get_reloader() -> Optional[ConfigReloader]:
    """Gibt die globale ConfigReloader-Instanz zurueck (oder None)."""
    return _global_reloader


def init_reloader(
    config_path: Optional[Path] = None,
    check_interval: float = 5.0,
) -> ConfigReloader:
    """
    Erstellt und speichert eine globale ConfigReloader-Instanz.

    Args:
        config_path: Pfad zur config.json. Standard: config/config.json.
        check_interval: Pruefintervall in Sekunden.

    Returns:
        Die erstellte ConfigReloader-Instanz.
    """
    global _global_reloader

    if config_path is None:
        from utils.config import CONFIG_DIR
        config_path = CONFIG_DIR / "config.json"

    _global_reloader = ConfigReloader(
        config_path=config_path,
        check_interval=check_interval,
    )

    logger.info(
        "Globaler ConfigReloader initialisiert: %s (Intervall: %.1fs)",
        config_path,
        check_interval,
    )

    return _global_reloader
