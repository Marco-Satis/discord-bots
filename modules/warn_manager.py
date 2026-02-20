"""
Warn Manager — Phase 11c
Verwaltet Verwarnungen (Warns) fuer Discord-User mit Punktesystem.

Jeder Warn hat eine Punktzahl. Bei Ueberschreitung konfigurierbarer
Schwellenwerte werden automatische Aktionen ausgeloest (Mute, Kick, Ban).
Warns verfallen nach einer einstellbaren Anzahl von Tagen (Standard: 30).

Dateiformat (data/admin/warns.json):
{
  "user_id_str": {
    "warns": [
      {
        "id": "uuid-string",
        "reason": "Grund",
        "points": 1,
        "warned_by": "AdminName",
        "warned_at": "2025-01-01T00:00:00",
        "expired": false
      },
      ...
    ],
    "total_points": 3
  }
}

Schwellenwerte (Standard):
  3 Punkte  -> Mute (Discord Timeout 1h)
  6 Punkte  -> Kick
  10 Punkte -> Ban
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR

logger = get_logger("warn_manager")

# Standard-Schwellenwerte fuer automatische Aktionen
DEFAULT_THRESHOLDS = {
    "mute": 3,    # Punkte fuer Auto-Mute (Discord Timeout 1h)
    "kick": 6,    # Punkte fuer Auto-Kick
    "ban": 10,    # Punkte fuer Auto-Ban
}

# Standard-Verfall in Tagen
DEFAULT_DECAY_DAYS = 30


class WarnManager:
    """
    Verwaltet Verwarnungen (Warns) mit Punktesystem und automatischem Verfall.

    Funktionen:
    - Warns hinzufuegen/entfernen mit Punktzahl
    - Automatischer Verfall nach konfigurierbarer Anzahl Tagen
    - Schwellenwert-Pruefung fuer automatische Aktionen
    - Persistenz via JSON-Datei in data/admin/warns.json

    Der Manager kuemmert sich nur um die Datenverwaltung.
    Das tatsaechliche Muten/Kicken/Bannen wird vom Cog uebernommen.
    """

    def __init__(
        self,
        data_file: Optional[Path] = None,
        thresholds: Optional[dict[str, int]] = None,
        decay_days: int = DEFAULT_DECAY_DAYS,
    ) -> None:
        """
        Args:
            data_file: Pfad zur JSON-Datei (Standard: data/admin/warns.json)
            thresholds: Schwellenwerte fuer Aktionen {"mute": 3, "kick": 6, "ban": 10}
            decay_days: Nach wie vielen Tagen ein Warn verfaellt (Standard: 30)
        """
        self.data_file = data_file or (ADMIN_DATA_DIR / "warns.json")
        self.thresholds = thresholds or DEFAULT_THRESHOLDS.copy()
        self.decay_days = decay_days
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistenz
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Warn-Daten von Disk laden"""
        try:
            if self.data_file.exists():
                with open(self.data_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                total_users = len(self._data)
                total_warns = sum(
                    len(entry.get("warns", []))
                    for entry in self._data.values()
                )
                logger.info(
                    f"Warn-Daten geladen: {total_users} User, "
                    f"{total_warns} Warns insgesamt"
                )
            else:
                self._data = {}
                logger.info("Keine Warn-Datei vorhanden, starte leer")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Warn-Daten laden fehlgeschlagen: {e}")
            self._data = {}

    def _save(self) -> None:
        """Warn-Daten auf Disk speichern"""
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Warn-Daten speichern fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Warn hinzufuegen
    # ------------------------------------------------------------------

    def add_warn(
        self,
        user_id: int,
        reason: str,
        points: int = 1,
        warned_by: str = "System",
    ) -> dict[str, Any]:
        """
        Neuen Warn fuer einen User hinzufuegen.

        Args:
            user_id: Discord-User-ID des Verwarnten
            reason: Grund fuer die Verwarnung
            points: Punktzahl (Standard: 1)
            warned_by: Display-Name des Admins der verwarnt

        Returns:
            Der erstellte Warn-Eintrag als dict
        """
        user_id_str = str(user_id)

        # User-Eintrag erstellen falls nicht vorhanden
        if user_id_str not in self._data:
            self._data[user_id_str] = {
                "warns": [],
                "total_points": 0,
            }

        # Warn-Eintrag erstellen
        warn_entry: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "reason": reason,
            "points": max(1, points),  # Mindestens 1 Punkt
            "warned_by": warned_by,
            "warned_at": datetime.now().isoformat(),
            "expired": False,
        }

        self._data[user_id_str]["warns"].append(warn_entry)

        # Gesamtpunkte neu berechnen (nur aktive Warns)
        self._recalculate_points(user_id_str)
        self._save()

        logger.info(
            f"Warn hinzugefuegt: User {user_id} — "
            f"{warn_entry['points']} Punkt(e) — {reason} "
            f"(von {warned_by}, Gesamt: {self._data[user_id_str]['total_points']})"
        )

        return warn_entry

    # ------------------------------------------------------------------
    # Warn entfernen
    # ------------------------------------------------------------------

    def remove_warn(self, user_id: int, warn_id: str) -> bool:
        """
        Bestimmten Warn eines Users entfernen.

        Args:
            user_id: Discord-User-ID
            warn_id: UUID des zu entfernenden Warns

        Returns:
            True wenn der Warn gefunden und entfernt wurde
        """
        user_id_str = str(user_id)

        if user_id_str not in self._data:
            return False

        warns = self._data[user_id_str]["warns"]
        original_count = len(warns)

        # Warn mit passender ID entfernen
        self._data[user_id_str]["warns"] = [
            w for w in warns if w.get("id") != warn_id
        ]

        if len(self._data[user_id_str]["warns"]) == original_count:
            # Kein Warn entfernt (ID nicht gefunden)
            return False

        # Gesamtpunkte neu berechnen
        self._recalculate_points(user_id_str)
        self._save()

        # User-Eintrag aufraemen wenn keine Warns mehr vorhanden
        if not self._data[user_id_str]["warns"]:
            del self._data[user_id_str]
            self._save()

        logger.info(f"Warn entfernt: User {user_id}, Warn-ID {warn_id}")
        return True

    # ------------------------------------------------------------------
    # Warns abfragen
    # ------------------------------------------------------------------

    def get_warns(self, user_id: int) -> list[dict[str, Any]]:
        """
        Aktive (nicht abgelaufene) Warns eines Users zurueckgeben.

        Args:
            user_id: Discord-User-ID

        Returns:
            Liste aktiver Warn-Eintraege (neueste zuerst)
        """
        user_id_str = str(user_id)
        if user_id_str not in self._data:
            return []

        active = [
            w for w in self._data[user_id_str]["warns"]
            if not w.get("expired", False)
        ]

        # Neueste zuerst sortieren
        active.sort(key=lambda w: w.get("warned_at", ""), reverse=True)
        return active

    def get_all_warns(self, user_id: int) -> list[dict[str, Any]]:
        """
        Alle Warns eines Users zurueckgeben (inklusive abgelaufener).

        Args:
            user_id: Discord-User-ID

        Returns:
            Liste aller Warn-Eintraege (neueste zuerst)
        """
        user_id_str = str(user_id)
        if user_id_str not in self._data:
            return []

        all_warns = self._data[user_id_str]["warns"].copy()
        all_warns.sort(key=lambda w: w.get("warned_at", ""), reverse=True)
        return all_warns

    def get_total_points(self, user_id: int) -> int:
        """
        Aktuelle Gesamtpunktzahl eines Users zurueckgeben (nur aktive Warns).

        Args:
            user_id: Discord-User-ID

        Returns:
            Gesamtpunktzahl aus aktiven Warns
        """
        user_id_str = str(user_id)
        if user_id_str not in self._data:
            return 0
        return self._data[user_id_str].get("total_points", 0)

    # ------------------------------------------------------------------
    # Verfall (Decay)
    # ------------------------------------------------------------------

    def check_expired(self, decay_days: Optional[int] = None) -> list[dict[str, Any]]:
        """
        Alte Warns als abgelaufen markieren.

        Warns die aelter als decay_days Tage sind werden als expired markiert.
        Bereits abgelaufene Warns werden uebersprungen.

        Args:
            decay_days: Verfall in Tagen (Standard: self.decay_days)

        Returns:
            Liste der neu als abgelaufen markierten Warn-Eintraege
        """
        if decay_days is None:
            decay_days = self.decay_days

        now = datetime.now()
        cutoff = now - timedelta(days=decay_days)
        newly_expired: list[dict[str, Any]] = []

        for user_id_str, user_data in self._data.items():
            for warn in user_data.get("warns", []):
                # Bereits abgelaufen — ueberspringen
                if warn.get("expired", False):
                    continue

                # Pruefen ob Warn aelter als cutoff ist
                warned_at_str = warn.get("warned_at", "")
                try:
                    warned_at = datetime.fromisoformat(warned_at_str)
                    if warned_at < cutoff:
                        warn["expired"] = True
                        newly_expired.append({
                            "user_id": user_id_str,
                            "warn_id": warn.get("id"),
                            "reason": warn.get("reason"),
                            "points": warn.get("points"),
                            "warned_at": warned_at_str,
                        })
                except (ValueError, TypeError):
                    logger.warning(
                        f"Ungueltiges warned_at fuer Warn {warn.get('id')}: "
                        f"{warned_at_str}"
                    )

        if newly_expired:
            # Gesamtpunkte aller betroffenen User neu berechnen
            affected_users = set(e["user_id"] for e in newly_expired)
            for user_id_str in affected_users:
                self._recalculate_points(user_id_str)

            self._save()
            logger.info(
                f"Warn-Verfall: {len(newly_expired)} Warns als abgelaufen markiert "
                f"(Schwelle: {decay_days} Tage)"
            )

        return newly_expired

    # ------------------------------------------------------------------
    # Schwellenwert-Pruefung
    # ------------------------------------------------------------------

    def get_threshold_action(self, user_id: int) -> Optional[str]:
        """
        Prueft ob die Gesamtpunktzahl eines Users einen Schwellenwert ueberschreitet.

        Gibt die hoechste ueberschrittene Aktion zurueck (ban > kick > mute).

        Args:
            user_id: Discord-User-ID

        Returns:
            "ban", "kick", "mute" oder None wenn kein Schwellenwert erreicht
        """
        total = self.get_total_points(user_id)

        if total >= self.thresholds.get("ban", 10):
            return "ban"
        if total >= self.thresholds.get("kick", 6):
            return "kick"
        if total >= self.thresholds.get("mute", 3):
            return "mute"

        return None

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _recalculate_points(self, user_id_str: str) -> None:
        """
        Gesamtpunkte eines Users neu berechnen (nur aktive/nicht-abgelaufene Warns).

        Args:
            user_id_str: User-ID als String-Key
        """
        if user_id_str not in self._data:
            return

        total = sum(
            w.get("points", 0)
            for w in self._data[user_id_str]["warns"]
            if not w.get("expired", False)
        )
        self._data[user_id_str]["total_points"] = total
