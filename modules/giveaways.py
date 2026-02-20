"""
Giveaway Manager — Phase 11h
Verwaltung von Giveaways (Verlosungen) mit Persistenz.

Speichert aktive und beendete Giveaways in data/admin/giveaways.json.
Der Manager kuemmert sich um die Daten-Logik (Teilnehmer, Gewinner,
Ablaufzeiten). Das Senden von Embeds und Button-Handling wird vom
Cog uebernommen.

Dateiformat (data/admin/giveaways.json):
{
  "message_id_str": {
    "channel_id": int,
    "guild_id": int,
    "prize": "Beschreibung des Preises",
    "winners_count": 1,
    "ends_at": "2025-01-01T12:00:00",
    "host_id": 123456,
    "participants": [user_id, ...],
    "winner_ids": [],
    "ended": false,
    "requirements": {
      "min_level": 0,
      "role_id": null,
      "min_days": 0
    }
  }
}
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR

logger = get_logger("modules.giveaways")

# Pfad zur Giveaway-Datendatei
DATA_FILE = ADMIN_DATA_DIR / "giveaways.json"


class GiveawayManager:
    """
    Verwaltung von Giveaways (Verlosungen).

    Funktionen:
    - Giveaway erstellen mit optionalen Anforderungen
    - Teilnehmer hinzufuegen/entfernen (Toggle)
    - Gewinner ziehen (zufaellig)
    - Giveaway vorzeitig beenden / erneut ziehen / abbrechen
    - Abgelaufene Giveaways ermitteln (fuer Background-Task)
    - Persistenz via JSON-Datei
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistenz
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Giveaway-Daten von Disk laden"""
        try:
            if DATA_FILE.exists():
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                active = sum(1 for g in self._data.values() if not g.get("ended"))
                logger.info(
                    f"Giveaway-Daten geladen: {len(self._data)} gesamt, "
                    f"{active} aktiv"
                )
            else:
                self._data = {}
                logger.info("Keine Giveaway-Datei vorhanden, starte leer")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Giveaway-Daten laden fehlgeschlagen: {e}")
            self._data = {}

    def _save(self) -> None:
        """Giveaway-Daten auf Disk speichern"""
        try:
            ADMIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Giveaway-Daten speichern fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Giveaway erstellen
    # ------------------------------------------------------------------

    def create(
        self,
        channel_id: int,
        guild_id: int,
        message_id: int,
        prize: str,
        duration_minutes: int,
        winners_count: int,
        host_id: int,
        requirements: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Neues Giveaway erstellen und speichern.

        Args:
            channel_id: ID des Discord-Kanals
            guild_id: ID des Discord-Servers
            message_id: ID der Giveaway-Nachricht (Embed mit Button)
            prize: Beschreibung des Preises
            duration_minutes: Laufzeit in Minuten
            winners_count: Anzahl der Gewinner
            host_id: Discord-ID des Veranstalters
            requirements: Optionale Anforderungen:
                - min_level: Mindest-Level (Standard: 0)
                - role_id: Erforderliche Rollen-ID (Standard: None)
                - min_days: Mindest-Mitgliedschaftsdauer in Tagen (Standard: 0)

        Returns:
            Das erstellte Giveaway-Dictionary
        """
        now = datetime.now()
        ends_at = now + timedelta(minutes=duration_minutes)

        # Anforderungen mit Defaults zusammenfuehren
        reqs = {
            "min_level": 0,
            "role_id": None,
            "min_days": 0,
        }
        if requirements:
            reqs.update(requirements)

        giveaway: dict[str, Any] = {
            "channel_id": channel_id,
            "guild_id": guild_id,
            "prize": prize,
            "winners_count": max(1, winners_count),
            "ends_at": ends_at.isoformat(),
            "created_at": now.isoformat(),
            "host_id": host_id,
            "participants": [],
            "winner_ids": [],
            "ended": False,
            "requirements": reqs,
        }

        message_id_str = str(message_id)
        self._data[message_id_str] = giveaway
        self._save()

        logger.info(
            f"Giveaway erstellt: '{prize}' (Message: {message_id}, "
            f"Dauer: {duration_minutes}min, Gewinner: {winners_count}, "
            f"Host: {host_id})"
        )

        return giveaway

    # ------------------------------------------------------------------
    # Teilnehmer verwalten
    # ------------------------------------------------------------------

    def add_participant(self, message_id: int, user_id: int) -> bool:
        """
        Teilnehmer zum Giveaway hinzufuegen.

        Args:
            message_id: ID der Giveaway-Nachricht
            user_id: Discord-ID des Teilnehmers

        Returns:
            True wenn erfolgreich hinzugefuegt, False wenn
            bereits teilgenommen oder Giveaway nicht gefunden/beendet
        """
        message_id_str = str(message_id)
        giveaway = self._data.get(message_id_str)

        if not giveaway:
            return False

        if giveaway.get("ended"):
            return False

        if user_id in giveaway["participants"]:
            return False

        giveaway["participants"].append(user_id)
        self._save()

        logger.debug(
            f"Giveaway {message_id}: Teilnehmer hinzugefuegt ({user_id}), "
            f"gesamt: {len(giveaway['participants'])}"
        )
        return True

    def remove_participant(self, message_id: int, user_id: int) -> bool:
        """
        Teilnehmer aus dem Giveaway entfernen.

        Args:
            message_id: ID der Giveaway-Nachricht
            user_id: Discord-ID des Teilnehmers

        Returns:
            True wenn erfolgreich entfernt, False wenn nicht
            teilgenommen oder Giveaway nicht gefunden/beendet
        """
        message_id_str = str(message_id)
        giveaway = self._data.get(message_id_str)

        if not giveaway:
            return False

        if giveaway.get("ended"):
            return False

        if user_id not in giveaway["participants"]:
            return False

        giveaway["participants"].remove(user_id)
        self._save()

        logger.debug(
            f"Giveaway {message_id}: Teilnehmer entfernt ({user_id}), "
            f"gesamt: {len(giveaway['participants'])}"
        )
        return True

    def is_participant(self, message_id: int, user_id: int) -> bool:
        """
        Pruefen ob ein User bereits teilnimmt.

        Args:
            message_id: ID der Giveaway-Nachricht
            user_id: Discord-ID des Users

        Returns:
            True wenn der User teilnimmt
        """
        message_id_str = str(message_id)
        giveaway = self._data.get(message_id_str)
        if not giveaway:
            return False
        return user_id in giveaway.get("participants", [])

    def get_participant_count(self, message_id: int) -> int:
        """
        Anzahl der Teilnehmer ermitteln.

        Args:
            message_id: ID der Giveaway-Nachricht

        Returns:
            Anzahl der Teilnehmer (0 wenn nicht gefunden)
        """
        message_id_str = str(message_id)
        giveaway = self._data.get(message_id_str)
        if not giveaway:
            return 0
        return len(giveaway.get("participants", []))

    # ------------------------------------------------------------------
    # Giveaway beenden
    # ------------------------------------------------------------------

    def end_giveaway(self, message_id: int) -> tuple[bool, list[int]]:
        """
        Giveaway beenden und Gewinner ziehen.

        Args:
            message_id: ID der Giveaway-Nachricht

        Returns:
            (success, winner_ids)
            - success: True wenn erfolgreich beendet
            - winner_ids: Liste der Gewinner-IDs (kann leer sein)
        """
        message_id_str = str(message_id)
        giveaway = self._data.get(message_id_str)

        if not giveaway:
            logger.warning(f"Giveaway {message_id} nicht gefunden")
            return False, []

        if giveaway.get("ended"):
            logger.warning(f"Giveaway {message_id} ist bereits beendet")
            return False, []

        # Gewinner ziehen
        winner_ids = self._pick_winners(giveaway)

        # Giveaway als beendet markieren
        giveaway["ended"] = True
        giveaway["ended_at"] = datetime.now().isoformat()
        giveaway["winner_ids"] = winner_ids
        self._save()

        prize = giveaway.get("prize", "?")
        logger.info(
            f"Giveaway beendet: '{prize}' (Message: {message_id}, "
            f"Gewinner: {winner_ids}, "
            f"Teilnehmer: {len(giveaway['participants'])})"
        )

        return True, winner_ids

    def reroll(self, message_id: int) -> tuple[bool, list[int]]:
        """
        Neue Gewinner fuer ein bereits beendetes Giveaway ziehen.

        Schliesst vorherige Gewinner von der neuen Ziehung aus.

        Args:
            message_id: ID der Giveaway-Nachricht

        Returns:
            (success, new_winner_ids)
            - success: True wenn erfolgreich
            - new_winner_ids: Liste der neuen Gewinner-IDs
        """
        message_id_str = str(message_id)
        giveaway = self._data.get(message_id_str)

        if not giveaway:
            logger.warning(f"Giveaway {message_id} nicht gefunden (reroll)")
            return False, []

        if not giveaway.get("ended"):
            logger.warning(f"Giveaway {message_id} ist noch aktiv (reroll)")
            return False, []

        # Vorherige Gewinner ausschliessen
        previous_winners = set(giveaway.get("winner_ids", []))
        eligible = [
            uid for uid in giveaway["participants"]
            if uid not in previous_winners
        ]

        winners_count = giveaway.get("winners_count", 1)

        if not eligible:
            logger.warning(
                f"Giveaway {message_id}: Keine weiteren Teilnehmer fuer Reroll"
            )
            return True, []

        # Neue Gewinner ziehen
        new_winners = random.sample(eligible, min(winners_count, len(eligible)))

        # Gewinner aktualisieren
        giveaway["winner_ids"] = new_winners
        giveaway["rerolled_at"] = datetime.now().isoformat()
        self._save()

        prize = giveaway.get("prize", "?")
        logger.info(
            f"Giveaway Reroll: '{prize}' (Message: {message_id}, "
            f"Neue Gewinner: {new_winners})"
        )

        return True, new_winners

    def cancel(self, message_id: int) -> bool:
        """
        Giveaway abbrechen (ohne Gewinner).

        Args:
            message_id: ID der Giveaway-Nachricht

        Returns:
            True wenn erfolgreich abgebrochen
        """
        message_id_str = str(message_id)
        giveaway = self._data.get(message_id_str)

        if not giveaway:
            logger.warning(f"Giveaway {message_id} nicht gefunden (cancel)")
            return False

        if giveaway.get("ended"):
            logger.warning(f"Giveaway {message_id} ist bereits beendet (cancel)")
            return False

        giveaway["ended"] = True
        giveaway["cancelled"] = True
        giveaway["ended_at"] = datetime.now().isoformat()
        giveaway["winner_ids"] = []
        self._save()

        prize = giveaway.get("prize", "?")
        logger.info(f"Giveaway abgebrochen: '{prize}' (Message: {message_id})")

        return True

    # ------------------------------------------------------------------
    # Abfragen
    # ------------------------------------------------------------------

    def get_giveaway(self, message_id: int) -> Optional[dict[str, Any]]:
        """
        Einzelnes Giveaway anhand der Message-ID zurueckgeben.

        Args:
            message_id: ID der Giveaway-Nachricht

        Returns:
            Giveaway-Dictionary oder None
        """
        message_id_str = str(message_id)
        giveaway = self._data.get(message_id_str)
        if giveaway:
            # Message-ID ins Dict aufnehmen fuer einfachere Verarbeitung
            result = giveaway.copy()
            result["message_id"] = int(message_id_str)
            return result
        return None

    def get_active(self) -> list[dict[str, Any]]:
        """
        Alle aktiven (nicht beendeten) Giveaways zurueckgeben.

        Returns:
            Liste von aktiven Giveaway-Dictionaries
        """
        active = []
        for msg_id_str, giveaway in self._data.items():
            if not giveaway.get("ended"):
                entry = giveaway.copy()
                entry["message_id"] = int(msg_id_str)
                active.append(entry)
        return active

    def get_expired(self) -> list[dict[str, Any]]:
        """
        Abgelaufene aber noch nicht beendete Giveaways zurueckgeben.

        Diese Methode wird vom Background-Task genutzt um
        Giveaways automatisch zu beenden.

        Returns:
            Liste von Giveaways deren ends_at < now und ended == False
        """
        now = datetime.now()
        expired = []

        for msg_id_str, giveaway in self._data.items():
            if giveaway.get("ended"):
                continue

            ends_at_str = giveaway.get("ends_at")
            if not ends_at_str:
                continue

            try:
                ends_at = datetime.fromisoformat(ends_at_str)
                if ends_at < now:
                    entry = giveaway.copy()
                    entry["message_id"] = int(msg_id_str)
                    expired.append(entry)
            except (ValueError, TypeError):
                logger.warning(
                    f"Ungueltiges ends_at fuer Giveaway {msg_id_str}: {ends_at_str}"
                )

        return expired

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _pick_winners(self, giveaway: dict[str, Any]) -> list[int]:
        """
        Zufaellige Gewinner aus den Teilnehmern ziehen.

        Args:
            giveaway: Das Giveaway-Dictionary

        Returns:
            Liste der Gewinner-User-IDs
        """
        participants = giveaway.get("participants", [])
        winners_count = giveaway.get("winners_count", 1)

        if not participants:
            return []

        # Nicht mehr Gewinner als Teilnehmer
        count = min(winners_count, len(participants))

        winners = random.sample(participants, count)
        return winners

    def cleanup_old(self, max_age_days: int = 30) -> int:
        """
        Alte beendete Giveaways entfernen.

        Args:
            max_age_days: Maximales Alter in Tagen (Standard: 30)

        Returns:
            Anzahl der entfernten Giveaways
        """
        now = datetime.now()
        to_remove = []

        for msg_id_str, giveaway in self._data.items():
            if not giveaway.get("ended"):
                continue

            ended_at_str = giveaway.get("ended_at")
            if not ended_at_str:
                continue

            try:
                ended_at = datetime.fromisoformat(ended_at_str)
                if (now - ended_at).days > max_age_days:
                    to_remove.append(msg_id_str)
            except (ValueError, TypeError):
                continue

        for msg_id_str in to_remove:
            del self._data[msg_id_str]

        if to_remove:
            self._save()
            logger.info(f"{len(to_remove)} alte Giveaways entfernt")

        return len(to_remove)
