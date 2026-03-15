"""
Giveaway Manager — Verwaltung von Giveaways (Verlosungen) mit SQLite-Persistenz

Persistenz: SQLite giveaways + giveaway_participants Tabellen (alleinige Datenquelle)
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from typing import Any, Optional

from utils.logger import get_logger
from modules.database.db_manager import get_db

logger = get_logger("modules.giveaways")


class GiveawayManager:
    """
    Verwaltung von Giveaways (Verlosungen).

    Persistenz: Alle Daten werden in SQLite gespeichert.

    Funktionen:
    - Giveaway erstellen mit optionalen Anforderungen
    - Teilnehmer hinzufuegen/entfernen (Toggle)
    - Gewinner ziehen (zufaellig)
    - Giveaway vorzeitig beenden / erneut ziehen / abbrechen
    - Abgelaufene Giveaways ermitteln (fuer Background-Task)
    - Persistenz via SQLite
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Persistenz — SQLite (alleinige Datenquelle)
    # ------------------------------------------------------------------

    async def load_from_db(self) -> bool:
        """
        Laedt Giveaways + Teilnehmer aus SQLite.

        Returns:
            True wenn SQLite-Daten erfolgreich geladen, False bei Fehler
        """
        try:
            db = await get_db()

            # --- Giveaways laden ---
            cursor = await db.execute(
                "SELECT id, message_id, channel_id, guild_id, prize, "
                "winner_count, ends_at, ended, created_at "
                "FROM giveaways "
                "ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()

            if not rows:
                self._data = {}
                logger.info("Keine Giveaway-Daten in SQLite — starte leer")
                return True

            # --- Alle Teilnehmer in einem Rutsch laden ---
            participant_cursor = await db.execute(
                "SELECT giveaway_id, user_id FROM giveaway_participants"
            )
            participant_rows = await participant_cursor.fetchall()

            # Teilnehmer nach giveaway_id gruppieren
            participants_map: dict[int, list[int]] = {}
            for p_row in participant_rows:
                gid = p_row[0]  # giveaway_id (INTEGER)
                uid_str = p_row[1]  # user_id (TEXT)
                try:
                    uid = int(uid_str)
                except (ValueError, TypeError):
                    continue
                participants_map.setdefault(gid, []).append(uid)

            # --- In-Memory-Daten aufbauen ---
            db_data: dict[str, dict[str, Any]] = {}
            for row in rows:
                db_id = row[0]          # id (INTEGER PK)
                msg_id = str(row[1])    # message_id (TEXT)
                channel_id = row[2]     # channel_id (TEXT)
                guild_id = row[3]       # guild_id (TEXT)
                prize = row[4]          # prize (TEXT)
                winner_count = row[5]   # winner_count (INTEGER)
                ends_at = row[6]        # ends_at (TIMESTAMP)
                ended = row[7]          # ended (BOOLEAN)
                created_at = row[8]     # created_at (TIMESTAMP)

                # Teilnehmer fuer dieses Giveaway
                giveaway_participants = participants_map.get(db_id, [])

                entry: dict[str, Any] = {
                    "channel_id": int(channel_id) if channel_id else 0,
                    "guild_id": int(guild_id) if guild_id else 0,
                    "prize": prize or "",
                    "winners_count": winner_count or 1,
                    "ends_at": ends_at or "",
                    "created_at": created_at or "",
                    "host_id": 0,
                    "participants": giveaway_participants,
                    "winner_ids": [],
                    "ended": bool(ended),
                    "requirements": {
                        "min_level": 0,
                        "role_id": None,
                        "min_days": 0,
                    },
                    "cancelled": False,
                    "ended_at": None,
                    "rerolled_at": None,
                    "_db_id": db_id,
                }

                db_data[msg_id] = entry

            self._data = db_data
            active = sum(1 for g in self._data.values() if not g.get("ended"))
            logger.info(
                f"SQLite geladen: {len(self._data)} Giveaways gesamt, "
                f"{active} aktiv, "
                f"{sum(len(g.get('participants', [])) for g in self._data.values())} Teilnehmer"
            )
            return True

        except Exception as e:
            logger.error(f"Giveaway-Daten aus SQLite laden fehlgeschlagen: {e}")
            return False

    # ------------------------------------------------------------------
    # Async DB Helper
    # ------------------------------------------------------------------

    def _fire_and_forget(self, coro) -> None:
        """Schedule an async DB write from synchronous code."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(coro)
            else:
                logger.debug("Event loop not running, skipping DB write")
        except RuntimeError:
            logger.debug("No event loop available, skipping DB write")

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


        # SQLite: INSERT giveaway
        self._fire_and_forget(self._db_create_giveaway(
            message_id_str, channel_id, guild_id, prize,
            max(1, winners_count), ends_at.isoformat(), now.isoformat()
        ))

        logger.info(
            f"Giveaway erstellt: '{prize}' (Message: {message_id}, "
            f"Dauer: {duration_minutes}min, Gewinner: {winners_count}, "
            f"Host: {host_id})"
        )

        return giveaway

    async def _db_create_giveaway(
        self, message_id: str, channel_id: int, guild_id: int,
        prize: str, winner_count: int, ends_at: str, created_at: str
    ) -> None:
        """INSERT giveaway into SQLite."""
        try:
            db = await get_db()
            await db.execute(
                "INSERT OR IGNORE INTO giveaways "
                "(message_id, channel_id, guild_id, prize, winner_count, "
                "ends_at, ended, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, FALSE, ?)",
                (message_id, str(channel_id), str(guild_id),
                 prize, winner_count, ends_at, created_at)
            )
            await db.commit()
            logger.debug(f"DB: Giveaway {message_id} erstellt")
        except Exception as e:
            logger.error(f"DB: Giveaway erstellen fehlgeschlagen: {e}")

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


        # SQLite: INSERT participant
        self._fire_and_forget(
            self._db_add_participant(message_id_str, user_id)
        )

        logger.debug(
            f"Giveaway {message_id}: Teilnehmer hinzugefuegt ({user_id}), "
            f"gesamt: {len(giveaway['participants'])}"
        )
        return True

    async def _db_add_participant(self, message_id: str, user_id: int) -> None:
        """INSERT participant into giveaway_participants via giveaway message_id."""
        try:
            db = await get_db()
            # Resolve giveaway_id from message_id
            cursor = await db.execute(
                "SELECT id FROM giveaways WHERE message_id = ?",
                (message_id,)
            )
            row = await cursor.fetchone()
            if not row:
                logger.warning(
                    f"DB: Giveaway {message_id} nicht in DB gefunden "
                    f"(add_participant)"
                )
                return

            giveaway_id = row[0]
            await db.execute(
                "INSERT OR IGNORE INTO giveaway_participants "
                "(giveaway_id, user_id) VALUES (?, ?)",
                (giveaway_id, str(user_id))
            )
            await db.commit()
            logger.debug(
                f"DB: Teilnehmer {user_id} zu Giveaway {message_id} hinzugefuegt"
            )
        except Exception as e:
            logger.error(f"DB: Teilnehmer hinzufuegen fehlgeschlagen: {e}")

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


        # SQLite: DELETE participant
        self._fire_and_forget(
            self._db_remove_participant(message_id_str, user_id)
        )

        logger.debug(
            f"Giveaway {message_id}: Teilnehmer entfernt ({user_id}), "
            f"gesamt: {len(giveaway['participants'])}"
        )
        return True

    async def _db_remove_participant(self, message_id: str, user_id: int) -> None:
        """DELETE participant from giveaway_participants via giveaway message_id."""
        try:
            db = await get_db()
            # Resolve giveaway_id from message_id
            cursor = await db.execute(
                "SELECT id FROM giveaways WHERE message_id = ?",
                (message_id,)
            )
            row = await cursor.fetchone()
            if not row:
                logger.warning(
                    f"DB: Giveaway {message_id} nicht in DB gefunden "
                    f"(remove_participant)"
                )
                return

            giveaway_id = row[0]
            await db.execute(
                "DELETE FROM giveaway_participants "
                "WHERE giveaway_id = ? AND user_id = ?",
                (giveaway_id, str(user_id))
            )
            await db.commit()
            logger.debug(
                f"DB: Teilnehmer {user_id} aus Giveaway {message_id} entfernt"
            )
        except Exception as e:
            logger.error(f"DB: Teilnehmer entfernen fehlgeschlagen: {e}")

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


        # SQLite: UPDATE ended=TRUE
        self._fire_and_forget(
            self._db_end_giveaway(message_id_str)
        )

        prize = giveaway.get("prize", "?")
        logger.info(
            f"Giveaway beendet: '{prize}' (Message: {message_id}, "
            f"Gewinner: {winner_ids}, "
            f"Teilnehmer: {len(giveaway['participants'])})"
        )

        return True, winner_ids

    async def _db_end_giveaway(self, message_id: str) -> None:
        """UPDATE giveaway ended=TRUE in SQLite."""
        try:
            db = await get_db()
            await db.execute(
                "UPDATE giveaways SET ended = TRUE WHERE message_id = ?",
                (message_id,)
            )
            await db.commit()
            logger.debug(f"DB: Giveaway {message_id} als beendet markiert")
        except Exception as e:
            logger.error(f"DB: Giveaway beenden fehlgeschlagen: {e}")

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


        # SQLite: Giveaway bleibt ended=TRUE, kein extra DB-Update noetig
        # (winner_ids sind nicht in der DB-Tabelle, nur in-memory)
        logger.debug(
            f"DB: Reroll fuer Giveaway {message_id} — keine DB-Aenderung "
            f"(winner_ids nur in-memory)"
        )

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


        # SQLite: UPDATE ended=TRUE (cancel = ended without winners)
        self._fire_and_forget(
            self._db_end_giveaway(message_id_str)
        )

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
    
            # SQLite: DELETE old giveaways
            self._fire_and_forget(
                self._db_cleanup_old(to_remove)
            )
            logger.info(f"{len(to_remove)} alte Giveaways entfernt")

        return len(to_remove)

    async def _db_cleanup_old(self, message_ids: list[str]) -> None:
        """DELETE old giveaways and their participants from SQLite."""
        try:
            db = await get_db()
            for msg_id in message_ids:
                # Erst giveaway_id ermitteln fuer Teilnehmer-Loesung
                cursor = await db.execute(
                    "SELECT id FROM giveaways WHERE message_id = ?",
                    (msg_id,)
                )
                row = await cursor.fetchone()
                if row:
                    giveaway_id = row[0]
                    await db.execute(
                        "DELETE FROM giveaway_participants WHERE giveaway_id = ?",
                        (giveaway_id,)
                    )
                await db.execute(
                    "DELETE FROM giveaways WHERE message_id = ?",
                    (msg_id,)
                )
            await db.commit()
            logger.debug(
                f"DB: {len(message_ids)} alte Giveaways + Teilnehmer geloescht"
            )
        except Exception as e:
            logger.error(f"DB: Alte Giveaways loeschen fehlgeschlagen: {e}")
