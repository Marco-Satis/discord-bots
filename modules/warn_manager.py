"""
Warn Manager — Phase 11c + SQLite Dual-Read
Verwaltet Verwarnungen (Warns) fuer Discord-User mit Punktesystem.

Jeder Warn hat eine Punktzahl. Bei Ueberschreitung konfigurierbarer
Schwellenwerte werden automatische Aktionen ausgeloest (Mute, Kick, Ban).
Warns verfallen nach einer einstellbaren Anzahl von Tagen (Standard: 30).

SQLite Dual-Read Modus:
  WRITE: Nur in SQLite `warns` Tabelle
  READ:  SQLite primaer, JSON Fallback (read-only)
  JSON-Datei wird NICHT geloescht (Fallback)

SQLite-Tabelle `warns`:
  id INTEGER PK AUTOINCREMENT
  user_id TEXT NOT NULL
  moderator_id TEXT NOT NULL
  reason TEXT NOT NULL
  points INTEGER DEFAULT 1
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  expires_at TIMESTAMP
  active BOOLEAN DEFAULT TRUE
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR
from modules.database.db_manager import get_db

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
    - SQLite primaer, JSON Fallback (read-only)

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
        self._db_loaded = False
        self._load()

    # ------------------------------------------------------------------
    # Persistenz
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Warn-Daten von JSON laden (Fallback-Daten)"""
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
                    f"Warn-Daten (JSON Fallback) geladen: {total_users} User, "
                    f"{total_warns} Warns insgesamt"
                )
            else:
                self._data = {}
                logger.info("Keine Warn-JSON-Datei vorhanden, starte leer")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Warn-Daten (JSON) laden fehlgeschlagen: {e}")
            self._data = {}

    async def load_from_db(self) -> None:
        """
        Laedt Warns aus SQLite und baut die interne Datenstruktur auf.
        Wird beim Cog-Load aufgerufen. Ueberschreibt die JSON-Daten
        falls SQLite-Daten vorhanden sind.
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT id, user_id, moderator_id, reason, points, "
                "created_at, expires_at, active FROM warns"
            )
            rows = await cursor.fetchall()

            if not rows:
                logger.info(
                    "Keine Warns in SQLite gefunden, verwende JSON-Fallback"
                )
                return

            # Interne Struktur aus SQLite-Daten aufbauen
            db_data: dict[str, dict[str, Any]] = {}

            for row in rows:
                user_id_str = str(row[1])  # user_id
                if user_id_str not in db_data:
                    db_data[user_id_str] = {
                        "warns": [],
                        "total_points": 0,
                    }

                # created_at als ISO-String
                created_at = row[5]
                if isinstance(created_at, datetime):
                    created_at_str = created_at.isoformat()
                elif created_at:
                    created_at_str = str(created_at)
                else:
                    created_at_str = datetime.now().isoformat()

                # active (SQLite) -> expired (intern) ist invertiert
                is_active = bool(row[7]) if row[7] is not None else True

                warn_entry: dict[str, Any] = {
                    "id": str(row[0]),       # SQLite INTEGER id als String
                    "reason": row[3] or "",  # reason
                    "points": row[4] or 1,   # points
                    "warned_by": row[2] or "System",  # moderator_id
                    "warned_at": created_at_str,
                    "expired": not is_active,
                    "_db_id": row[0],        # Original DB-ID fuer Updates
                }

                db_data[user_id_str]["warns"].append(warn_entry)

            # Gesamtpunkte berechnen
            for user_id_str in db_data:
                total = sum(
                    w.get("points", 0)
                    for w in db_data[user_id_str]["warns"]
                    if not w.get("expired", False)
                )
                db_data[user_id_str]["total_points"] = total

            self._data = db_data
            self._db_loaded = True

            total_users = len(db_data)
            total_warns = sum(
                len(entry["warns"]) for entry in db_data.values()
            )
            logger.info(
                f"Warn-Daten aus SQLite geladen: {total_users} User, "
                f"{total_warns} Warns insgesamt"
            )

        except Exception as e:
            logger.error(
                f"Warn-Daten aus SQLite laden fehlgeschlagen, "
                f"verwende JSON-Fallback: {e}",
                exc_info=True,
            )

    def _save(self) -> None:
        """No-op: Schreibt nicht mehr nach JSON. Alle Writes gehen nach SQLite."""
        pass

    # ------------------------------------------------------------------
    # SQLite Hilfs-Methoden
    # ------------------------------------------------------------------

    def _fire_and_forget(self, coro) -> None:
        """
        Startet eine Coroutine als fire-and-forget Task.
        Fuer sync Methoden die DB-Writes ausfuehren muessen.
        """
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(coro)
            # Fehler loggen statt schlucken
            task.add_done_callback(self._task_error_handler)
        except RuntimeError:
            # Kein laufender Event-Loop (z.B. Unit-Tests)
            logger.warning(
                "Kein laufender Event-Loop fuer DB-Write, "
                "Aenderung nur im Speicher"
            )

    @staticmethod
    def _task_error_handler(task: asyncio.Task) -> None:
        """Callback fuer fire-and-forget Tasks — loggt Fehler."""
        try:
            exc = task.exception()
            if exc:
                logger.error(
                    f"Fehler in fire-and-forget DB-Task: {exc}",
                    exc_info=exc,
                )
        except asyncio.CancelledError:
            pass

    async def _db_insert_warn(
        self,
        user_id: str,
        moderator_id: str,
        reason: str,
        points: int,
        created_at: str,
    ) -> Optional[int]:
        """
        Fuegt einen Warn in die SQLite-Datenbank ein.

        Returns:
            Die neue DB-ID oder None bei Fehler
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                "INSERT INTO warns (user_id, moderator_id, reason, points, created_at, active) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, moderator_id, reason, points, created_at, True),
            )
            await db.commit()
            new_id = cursor.lastrowid
            logger.debug(f"Warn in SQLite eingefuegt: ID {new_id}, User {user_id}")
            return new_id
        except Exception as e:
            logger.error(f"SQLite INSERT warn fehlgeschlagen: {e}", exc_info=True)
            return None

    async def _db_deactivate_warn(self, db_id: int) -> bool:
        """
        Setzt active=FALSE fuer einen Warn in SQLite.

        Args:
            db_id: Die SQLite-ID des Warns

        Returns:
            True bei Erfolg
        """
        try:
            db = await get_db()
            await db.execute(
                "UPDATE warns SET active = FALSE WHERE id = ?",
                (db_id,),
            )
            await db.commit()
            logger.debug(f"Warn in SQLite deaktiviert: ID {db_id}")
            return True
        except Exception as e:
            logger.error(f"SQLite UPDATE warn deactivate fehlgeschlagen: {e}", exc_info=True)
            return False

    async def _db_expire_warns(self, db_ids: list[int]) -> bool:
        """
        Setzt active=FALSE fuer mehrere abgelaufene Warns in SQLite.

        Args:
            db_ids: Liste von SQLite-IDs

        Returns:
            True bei Erfolg
        """
        if not db_ids:
            return True
        try:
            db = await get_db()
            placeholders = ",".join("?" for _ in db_ids)
            await db.execute(
                f"UPDATE warns SET active = FALSE WHERE id IN ({placeholders})",
                db_ids,
            )
            await db.commit()
            logger.debug(f"Warns in SQLite abgelaufen markiert: {db_ids}")
            return True
        except Exception as e:
            logger.error(f"SQLite UPDATE warns expire fehlgeschlagen: {e}", exc_info=True)
            return False

    async def _db_delete_warn(self, db_id: int) -> bool:
        """
        Loescht einen Warn aus SQLite (fuer remove_warn).

        Args:
            db_id: Die SQLite-ID des Warns

        Returns:
            True bei Erfolg
        """
        try:
            db = await get_db()
            await db.execute(
                "UPDATE warns SET active = FALSE WHERE id = ?",
                (db_id,),
            )
            await db.commit()
            logger.debug(f"Warn in SQLite als inaktiv markiert (remove): ID {db_id}")
            return True
        except Exception as e:
            logger.error(f"SQLite UPDATE warn (remove) fehlgeschlagen: {e}", exc_info=True)
            return False

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

        Schreibt in SQLite (fire-and-forget) und aktualisiert internen Cache.

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
        now_iso = datetime.now().isoformat()
        warn_entry: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "reason": reason,
            "points": max(1, points),  # Mindestens 1 Punkt
            "warned_by": warned_by,
            "warned_at": now_iso,
            "expired": False,
        }

        self._data[user_id_str]["warns"].append(warn_entry)

        # Gesamtpunkte neu berechnen (nur aktive Warns)
        self._recalculate_points(user_id_str)

        logger.info(
            f"Warn hinzugefuegt: User {user_id} — "
            f"{warn_entry['points']} Punkt(e) — {reason} "
            f"(von {warned_by}, Gesamt: {self._data[user_id_str]['total_points']})"
        )

        # SQLite INSERT fire-and-forget
        self._fire_and_forget(
            self._db_insert_warn(
                user_id=user_id_str,
                moderator_id=warned_by,
                reason=reason,
                points=warn_entry["points"],
                created_at=now_iso,
            )
        )

        return warn_entry

    # ------------------------------------------------------------------
    # Warn entfernen
    # ------------------------------------------------------------------

    def remove_warn(self, user_id: int, warn_id: str) -> bool:
        """
        Bestimmten Warn eines Users entfernen.

        Setzt active=FALSE in SQLite (fire-and-forget) und entfernt aus internem Cache.

        Args:
            user_id: Discord-User-ID
            warn_id: UUID oder DB-ID des zu entfernenden Warns

        Returns:
            True wenn der Warn gefunden und entfernt wurde
        """
        user_id_str = str(user_id)

        if user_id_str not in self._data:
            return False

        warns = self._data[user_id_str]["warns"]
        original_count = len(warns)

        # Warn mit passender ID finden (und DB-ID merken fuer SQLite)
        removed_db_id = None
        new_warns = []
        for w in warns:
            if w.get("id") == warn_id:
                removed_db_id = w.get("_db_id")
            else:
                new_warns.append(w)

        self._data[user_id_str]["warns"] = new_warns

        if len(self._data[user_id_str]["warns"]) == original_count:
            # Kein Warn entfernt (ID nicht gefunden)
            return False

        # Gesamtpunkte neu berechnen
        self._recalculate_points(user_id_str)

        # User-Eintrag aufraemen wenn keine Warns mehr vorhanden
        if not self._data[user_id_str]["warns"]:
            del self._data[user_id_str]

        logger.info(f"Warn entfernt: User {user_id}, Warn-ID {warn_id}")

        # SQLite UPDATE active=FALSE fire-and-forget
        if removed_db_id is not None:
            self._fire_and_forget(self._db_delete_warn(removed_db_id))
        else:
            # Fallback: Versuche per warn_id (die als String-ID im Feld steht) zu deaktivieren
            # Dies greift wenn die ID eine DB-ID als String ist
            try:
                db_id_int = int(warn_id)
                self._fire_and_forget(self._db_delete_warn(db_id_int))
            except (ValueError, TypeError):
                logger.warning(
                    f"Warn {warn_id} entfernt, aber keine DB-ID fuer SQLite-Update gefunden"
                )

        return True

    # ------------------------------------------------------------------
    # Warns abfragen
    # ------------------------------------------------------------------

    def get_warns(self, user_id: int) -> list[dict[str, Any]]:
        """
        Aktive (nicht abgelaufene) Warns eines Users zurueckgeben.
        Liest primaer aus internem Cache (befuellt aus SQLite oder JSON-Fallback).

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

    async def get_warns_from_db(self, user_id: int) -> list[dict[str, Any]]:
        """
        Aktive Warns direkt aus SQLite lesen (Bypass fuer Cache).
        Faellt auf internen Cache zurueck bei DB-Fehler.

        Args:
            user_id: Discord-User-ID

        Returns:
            Liste aktiver Warn-Eintraege (neueste zuerst)
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT id, user_id, moderator_id, reason, points, "
                "created_at, active FROM warns "
                "WHERE user_id = ? AND active = TRUE "
                "ORDER BY created_at DESC",
                (str(user_id),),
            )
            rows = await cursor.fetchall()

            if rows:
                result = []
                for row in rows:
                    created_at = row[5]
                    if isinstance(created_at, datetime):
                        created_at_str = created_at.isoformat()
                    elif created_at:
                        created_at_str = str(created_at)
                    else:
                        created_at_str = ""

                    result.append({
                        "id": str(row[0]),
                        "reason": row[3] or "",
                        "points": row[4] or 1,
                        "warned_by": row[2] or "System",
                        "warned_at": created_at_str,
                        "expired": False,
                        "_db_id": row[0],
                    })
                return result

            # Keine Ergebnisse in DB — Fallback auf Cache
            return self.get_warns(user_id)

        except Exception as e:
            logger.error(f"SQLite get_warns fehlgeschlagen, Fallback auf Cache: {e}")
            return self.get_warns(user_id)

    def get_all_warns(self, user_id: int) -> list[dict[str, Any]]:
        """
        Alle Warns eines Users zurueckgeben (inklusive abgelaufener).
        Liest primaer aus internem Cache (befuellt aus SQLite oder JSON-Fallback).

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

    async def get_all_warns_from_db(self, user_id: int) -> list[dict[str, Any]]:
        """
        Alle Warns (inkl. abgelaufener) direkt aus SQLite lesen.
        Faellt auf internen Cache zurueck bei DB-Fehler.

        Args:
            user_id: Discord-User-ID

        Returns:
            Liste aller Warn-Eintraege (neueste zuerst)
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT id, user_id, moderator_id, reason, points, "
                "created_at, active FROM warns "
                "WHERE user_id = ? "
                "ORDER BY created_at DESC",
                (str(user_id),),
            )
            rows = await cursor.fetchall()

            if rows:
                result = []
                for row in rows:
                    created_at = row[5]
                    if isinstance(created_at, datetime):
                        created_at_str = created_at.isoformat()
                    elif created_at:
                        created_at_str = str(created_at)
                    else:
                        created_at_str = ""

                    is_active = bool(row[6]) if row[6] is not None else True
                    result.append({
                        "id": str(row[0]),
                        "reason": row[3] or "",
                        "points": row[4] or 1,
                        "warned_by": row[2] or "System",
                        "warned_at": created_at_str,
                        "expired": not is_active,
                        "_db_id": row[0],
                    })
                return result

            # Keine Ergebnisse in DB — Fallback auf Cache
            return self.get_all_warns(user_id)

        except Exception as e:
            logger.error(f"SQLite get_all_warns fehlgeschlagen, Fallback auf Cache: {e}")
            return self.get_all_warns(user_id)

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
        Aktualisiert auch SQLite (active=FALSE) via fire-and-forget.

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
        expired_db_ids: list[int] = []

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
                        # DB-ID merken fuer SQLite-Update
                        db_id = warn.get("_db_id")
                        if db_id is not None:
                            expired_db_ids.append(db_id)
                        else:
                            # Fallback: versuche die String-ID als int zu parsen
                            try:
                                expired_db_ids.append(int(warn.get("id", "")))
                            except (ValueError, TypeError):
                                pass
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

            logger.info(
                f"Warn-Verfall: {len(newly_expired)} Warns als abgelaufen markiert "
                f"(Schwelle: {decay_days} Tage)"
            )

            # SQLite UPDATE fire-and-forget
            if expired_db_ids:
                self._fire_and_forget(self._db_expire_warns(expired_db_ids))

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
