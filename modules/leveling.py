"""
Leveling Manager — XP- und Level-System fuer den Admin Bot

Verwaltet Erfahrungspunkte (XP) und Level fuer Server-Mitglieder.
XP werden durch Nachrichten und Voice-Aktivitaet gesammelt.
Bei Level-Ups koennen automatisch Rollen vergeben werden.

Persistenz:
  - Userdaten:     SQLite leveling-Tabelle (alleinige Datenquelle)
  - Einstellungen: data/admin/leveling_config.json (bleibt JSON)

SQLite-Tabelle leveling:
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT UNIQUE NOT NULL,
  xp INTEGER DEFAULT 0,
  level INTEGER DEFAULT 0,
  messages INTEGER DEFAULT 0,
  voice_minutes INTEGER DEFAULT 0,
  last_xp_time TIMESTAMP

Level-Formel: xp_needed = 5 * level^2 + 50 * level + 100
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR
from utils.async_tasks import schedule_from_sync
from modules.database.db_manager import get_db

logger = get_logger("leveling_manager")

# Standard-Pfade
LEVELING_CONFIG_FILE = ADMIN_DATA_DIR / "leveling_config.json"


def _default_config() -> dict[str, Any]:
    """Standard-Konfiguration fuer das Leveling-System zurueckgeben."""
    return {
        "xp_per_message_min": 15,
        "xp_per_message_max": 25,
        "xp_cooldown_seconds": 60,
        "voice_xp_per_minute": 5,
        "level_formula": "5 * level^2 + 50 * level + 100",
        "channel_multipliers": {},
        "role_rewards": {},
    }


class LevelingManager:
    """
    Verwaltet XP, Level und Belohnungen fuer Server-Mitglieder.

    Persistenz:
    - Liest aus SQLite (leveling-Tabelle)
    - Schreibt alle Aenderungen in SQLite (fire-and-forget)
    - Konfiguration bleibt vollstaendig in JSON

    Funktionen:
    - XP fuer Nachrichten vergeben (mit Cooldown und Zufallsrange)
    - XP fuer Voice-Aktivitaet vergeben
    - Automatische Level-Berechnung
    - Channel-Multiplikatoren (z.B. 2x XP in bestimmten Kanaelen)
    - Rollen-Belohnungen bei Level-Ups
    - Leaderboard-Abfrage (SQLite-Query)
    - Rang-Abfrage (SQLite-Query)

    Der Manager kuemmert sich um die Datenverwaltung.
    Das Vergeben von Rollen und Senden von Nachrichten
    wird vom Cog uebernommen.
    """

    def __init__(
        self,
        config_file: Path | None = None,
        **kwargs,
    ) -> None:
        """
        Args:
            config_file: Pfad zur Config-JSON (Standard: data/admin/leveling_config.json)
        """
        self.config_file = config_file or LEVELING_CONFIG_FILE
        self._data: dict[str, dict[str, Any]] = {}
        self._config: dict[str, Any] = _default_config()
        self._load_config()

    # ------------------------------------------------------------------
    # Persistence — SQLite (alleinige Datenquelle)
    # ------------------------------------------------------------------

    async def load_from_db(self) -> bool:
        """
        Leveling-Daten aus SQLite laden und in _data uebernehmen.

        Returns:
            True wenn erfolgreich geladen, False bei Fehler
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT user_id, xp, level, messages, voice_minutes, last_xp_time "
                "FROM leveling"
            )
            rows = await cursor.fetchall()

            if not rows:
                self._data = {}
                logger.info("Keine Leveling-Daten in SQLite — starte leer")
                return True

            # SQLite-Daten in internes Format konvertieren
            loaded: dict[str, dict[str, Any]] = {}
            for row in rows:
                user_id = str(row["user_id"])
                loaded[user_id] = {
                    "xp": row["xp"] or 0,
                    "level": row["level"] or 0,
                    "total_messages": row["messages"] or 0,
                    "voice_minutes": row["voice_minutes"] or 0,
                    "last_xp_at": row["last_xp_time"],
                }

            self._data = loaded
            logger.info(f"Leveling-Daten aus SQLite geladen: {len(loaded)} User")
            return True

        except Exception as e:
            logger.error(f"Leveling-Daten aus SQLite laden fehlgeschlagen: {e}")
            return False

    # ------------------------------------------------------------------
    # Fire-and-Forget — sync -> async Bruecke
    # ------------------------------------------------------------------

    def _fire_and_forget(self, coro) -> None:
        """
        Plant eine async Coroutine als getrackten Background-Task.
        Wird aus synchronem Code aufgerufen (add_message_xp, etc.).

        Reference-Tracking via utils.async_tasks gegen GC-Verlust
        (audit-fix 2026-05-17, async.md H2).
        """
        schedule_from_sync(coro, name="leveling.fire_and_forget")

    async def _db_upsert_user(self, user_id: int, user_data: dict[str, Any]) -> None:
        """
        User-Daten in SQLite einfuegen oder aktualisieren (INSERT OR REPLACE).

        Args:
            user_id: Discord-User-ID
            user_data: Dict mit xp, level, total_messages, voice_minutes, last_xp_at
        """
        try:
            db = await get_db()
            await db.execute(
                """
                INSERT INTO leveling (user_id, xp, level, messages, voice_minutes, last_xp_time)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    xp = excluded.xp,
                    level = excluded.level,
                    messages = excluded.messages,
                    voice_minutes = excluded.voice_minutes,
                    last_xp_time = excluded.last_xp_time
                """,
                (
                    str(user_id),
                    user_data.get("xp", 0),
                    user_data.get("level", 0),
                    user_data.get("total_messages", 0),
                    user_data.get("voice_minutes", 0),
                    user_data.get("last_xp_at"),
                ),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"SQLite-Upsert fuer User {user_id} fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Persistence — Konfiguration (bleibt JSON)
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        """Leveling-Konfiguration von Disk laden."""
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # Defaults fuer fehlende Schluessel einsetzen
                defaults = _default_config()
                for key, value in defaults.items():
                    loaded.setdefault(key, value)
                self._config = loaded
                logger.info("Leveling-Konfiguration geladen")
            else:
                logger.info(
                    "Keine Leveling-Config vorhanden, verwende Standardwerte"
                )
                self._save_config()
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Leveling-Config laden fehlgeschlagen: {e}")

    def _save_config(self) -> None:
        """Leveling-Konfiguration auf Disk speichern."""
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Leveling-Config speichern fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Level-Formel
    # ------------------------------------------------------------------

    @staticmethod
    def xp_for_level(level: int) -> int:
        """
        Berechnet die benoetigten XP fuer ein bestimmtes Level.

        Formel: 5 * level^2 + 50 * level + 100

        Args:
            level: Das Ziel-Level

        Returns:
            Benoetigte XP um dieses Level zu erreichen
        """
        return 5 * level * level + 50 * level + 100

    # ------------------------------------------------------------------
    # User-Daten
    # ------------------------------------------------------------------

    def _ensure_user(self, user_id: int) -> dict[str, Any]:
        """
        Sicherstellen dass ein User-Eintrag existiert.

        Args:
            user_id: Discord-User-ID

        Returns:
            User-Daten-Dictionary (Referenz auf den internen Eintrag)
        """
        uid = str(user_id)
        if uid not in self._data:
            self._data[uid] = {
                "xp": 0,
                "level": 0,
                "total_messages": 0,
                "voice_minutes": 0,
                "last_xp_at": None,
            }
        return self._data[uid]

    def get_user(self, user_id: int) -> dict[str, Any]:
        """
        User-Daten zurueckgeben (Kopie).

        Args:
            user_id: Discord-User-ID

        Returns:
            Dict mit xp, level, total_messages, voice_minutes, last_xp_at
        """
        uid = str(user_id)
        if uid in self._data:
            return dict(self._data[uid])
        # Leerer Standard-User
        return {
            "xp": 0,
            "level": 0,
            "total_messages": 0,
            "voice_minutes": 0,
            "last_xp_at": None,
        }

    # ------------------------------------------------------------------
    # XP vergeben — Nachrichten
    # ------------------------------------------------------------------

    def add_message_xp(
        self,
        user_id: int,
        channel_id: int | None = None,
    ) -> tuple[int, bool]:
        """
        XP fuer eine Nachricht vergeben (mit Cooldown-Pruefung).

        Vergibt zufaellig xp_per_message_min bis xp_per_message_max XP.
        Beachtet den Cooldown (xp_cooldown_seconds) — innerhalb des
        Cooldowns werden 0 XP vergeben.
        Optional wird ein Channel-Multiplikator angewendet.

        Schreibt Aenderungen via fire-and-forget in SQLite.

        Args:
            user_id: Discord-User-ID
            channel_id: Optional, Channel-ID fuer Multiplikator

        Returns:
            (xp_gained, leveled_up)
            - xp_gained: Tatsaechlich vergebene XP (0 wenn Cooldown aktiv)
            - leveled_up: True wenn der User ein neues Level erreicht hat
        """
        user = self._ensure_user(user_id)
        user["total_messages"] += 1

        # Cooldown pruefen
        now = datetime.now()
        cooldown = self._config.get("xp_cooldown_seconds", 60)
        last_xp_str = user.get("last_xp_at")

        if last_xp_str:
            try:
                last_xp = datetime.fromisoformat(last_xp_str)
                elapsed = (now - last_xp).total_seconds()
                if elapsed < cooldown:
                    # Nur messages zaehlt hoch, kein XP — trotzdem DB-Update
                    self._fire_and_forget(self._db_upsert_user(user_id, user))
                    return 0, False
            except (ValueError, TypeError):
                pass  # Ungueltiges Datum ignorieren, XP trotzdem geben

        # XP berechnen
        xp_min = self._config.get("xp_per_message_min", 15)
        xp_max = self._config.get("xp_per_message_max", 25)
        xp = random.randint(xp_min, xp_max)

        # Channel-Multiplikator anwenden
        if channel_id:
            multipliers = self._config.get("channel_multipliers", {})
            multiplier = multipliers.get(str(channel_id), 1.0)
            xp = int(xp * multiplier)

        # XP hinzufuegen und Level pruefen
        user["xp"] += xp
        user["last_xp_at"] = now.isoformat()

        leveled_up = self._check_level_up(user)

        # Fire-and-forget: SQLite-Update
        self._fire_and_forget(self._db_upsert_user(user_id, user))

        return xp, leveled_up

    # ------------------------------------------------------------------
    # XP vergeben — Voice
    # ------------------------------------------------------------------

    def add_voice_xp(
        self,
        user_id: int,
        minutes: int,
    ) -> tuple[int, bool]:
        """
        XP fuer Voice-Aktivitaet vergeben.

        Schreibt Aenderungen via fire-and-forget in SQLite.

        Args:
            user_id: Discord-User-ID
            minutes: Anzahl Minuten im Voice-Channel

        Returns:
            (xp_gained, leveled_up)
            - xp_gained: Vergebene XP
            - leveled_up: True wenn der User ein neues Level erreicht hat
        """
        if minutes <= 0:
            return 0, False

        user = self._ensure_user(user_id)
        xp_per_min = self._config.get("voice_xp_per_minute", 5)
        xp = xp_per_min * minutes

        user["xp"] += xp
        user["voice_minutes"] += minutes

        leveled_up = self._check_level_up(user)

        # Fire-and-forget: SQLite-Update
        self._fire_and_forget(self._db_upsert_user(user_id, user))

        return xp, leveled_up

    # ------------------------------------------------------------------
    # XP direkt setzen (Admin-Funktion)
    # ------------------------------------------------------------------

    def set_xp(self, user_id: int, amount: int) -> None:
        """
        XP eines Users direkt setzen (Admin-Funktion).

        Berechnet das Level automatisch neu.
        Schreibt Aenderungen via fire-and-forget in SQLite.

        Args:
            user_id: Discord-User-ID
            amount: Neue XP-Anzahl (muss >= 0 sein)
        """
        if amount < 0:
            amount = 0

        user = self._ensure_user(user_id)
        user["xp"] = amount

        # Level komplett neu berechnen
        level = 0
        while user["xp"] >= self.xp_for_level(level):
            level += 1
        # Level ist jetzt eins zu hoch (erste Stufe die NICHT erreicht wird)
        # Korrektur: Level ist die Anzahl der vollstaendig erreichten Stufen
        user["level"] = max(0, level - 1) if level > 0 else 0

        # Nochmal pruefen: Wenn genug XP fuer naechstes Level
        while user["xp"] >= self.xp_for_level(user["level"]):
            user["level"] += 1

        # Fire-and-forget: SQLite-Update
        self._fire_and_forget(self._db_upsert_user(user_id, user))

        logger.info(f"XP manuell gesetzt: User {user_id} -> {amount} XP (Level {user['level']})")

    # ------------------------------------------------------------------
    # Level-Berechnung
    # ------------------------------------------------------------------

    def _check_level_up(self, user: dict[str, Any]) -> bool:
        """
        Pruefen ob der User ein Level-Up verdient hat.

        Erhoet das Level so lange, bis die XP nicht mehr ausreichen.

        Args:
            user: Referenz auf den User-Datensatz

        Returns:
            True wenn mindestens ein Level-Up stattgefunden hat
        """
        leveled_up = False
        current_level = user.get("level", 0)
        xp = user.get("xp", 0)

        while xp >= self.xp_for_level(current_level):
            current_level += 1
            leveled_up = True

        if leveled_up:
            user["level"] = current_level

        return leveled_up

    # ------------------------------------------------------------------
    # Rollen-Belohnungen
    # ------------------------------------------------------------------

    def get_role_reward(self, level: int) -> int | None:
        """
        Rollen-ID fuer ein bestimmtes Level zurueckgeben.

        Args:
            level: Das erreichte Level

        Returns:
            Discord-Rollen-ID oder None wenn keine Belohnung
        """
        rewards = self._config.get("role_rewards", {})
        role_id = rewards.get(str(level))
        if role_id is not None:
            return int(role_id)
        return None

    def set_role_reward(self, level: int, role_id: int) -> None:
        """
        Rollen-Belohnung fuer ein Level setzen.

        Args:
            level: Das Level bei dem die Rolle vergeben wird
            role_id: Discord-Rollen-ID
        """
        if "role_rewards" not in self._config:
            self._config["role_rewards"] = {}
        self._config["role_rewards"][str(level)] = role_id
        self._save_config()
        logger.info(f"Rollen-Belohnung gesetzt: Level {level} -> Rolle {role_id}")

    # ------------------------------------------------------------------
    # Channel-Multiplikatoren
    # ------------------------------------------------------------------

    def set_channel_multiplier(self, channel_id: int, factor: float) -> None:
        """
        XP-Multiplikator fuer einen Channel setzen.

        Args:
            channel_id: Discord-Channel-ID
            factor: Multiplikator (z.B. 1.5 = 50% mehr XP, 0.5 = halbe XP)
        """
        if "channel_multipliers" not in self._config:
            self._config["channel_multipliers"] = {}

        if factor == 1.0:
            # Standard-Wert: Eintrag entfernen
            self._config["channel_multipliers"].pop(str(channel_id), None)
        else:
            self._config["channel_multipliers"][str(channel_id)] = factor

        self._save_config()
        logger.info(f"Channel-Multiplikator gesetzt: {channel_id} -> {factor}x")

    def get_channel_multiplier(self, channel_id: int) -> float:
        """
        XP-Multiplikator fuer einen Channel abfragen.

        Args:
            channel_id: Discord-Channel-ID

        Returns:
            Multiplikator als Float (Standard: 1.0)
        """
        multipliers = self._config.get("channel_multipliers", {})
        return float(multipliers.get(str(channel_id), 1.0))

    # ------------------------------------------------------------------
    # Leaderboard (SQLite-Query mit In-Memory-Fallback)
    # ------------------------------------------------------------------

    def get_leaderboard(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        Top-Spieler nach XP sortiert zurueckgeben.

        Versucht SQLite-Query (fire-and-forget Pattern nicht moeglich
        da Rueckgabewert benoetigt). Faellt auf In-Memory-Daten zurueck.

        Args:
            limit: Maximale Anzahl Eintraege (Standard: 10)

        Returns:
            Liste von Dicts mit user_id, xp, level, total_messages, voice_minutes
            Sortiert nach XP absteigend
        """
        # Versuche synchronen Zugriff auf SQLite-Ergebnis via In-Memory-Cache
        # Die _data wird durch load_from_db() aus SQLite befuellt
        entries: list[dict[str, Any]] = []

        for uid, data in self._data.items():
            entries.append({
                "user_id": int(uid),
                "xp": data.get("xp", 0),
                "level": data.get("level", 0),
                "total_messages": data.get("total_messages", 0),
                "voice_minutes": data.get("voice_minutes", 0),
            })

        # Nach XP absteigend sortieren
        entries.sort(key=lambda e: e["xp"], reverse=True)
        return entries[:limit]

    async def get_leaderboard_db(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        Top-Spieler direkt aus SQLite abfragen (async Version).

        Fuer Verwendung in async-Kontexten (z.B. Slash-Commands).
        Faellt auf In-Memory-Daten zurueck bei DB-Fehler.

        Args:
            limit: Maximale Anzahl Eintraege (Standard: 10)

        Returns:
            Liste von Dicts mit user_id, xp, level, total_messages, voice_minutes
            Sortiert nach XP absteigend
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT user_id, xp, level, messages, voice_minutes "
                "FROM leveling "
                "ORDER BY xp DESC "
                "LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()

            if not rows:
                # DB leer — Fallback auf In-Memory
                return self.get_leaderboard(limit)

            entries: list[dict[str, Any]] = []
            for row in rows:
                entries.append({
                    "user_id": int(row["user_id"]),
                    "xp": row["xp"] or 0,
                    "level": row["level"] or 0,
                    "total_messages": row["messages"] or 0,
                    "voice_minutes": row["voice_minutes"] or 0,
                })
            return entries

        except Exception as e:
            logger.error(f"Leaderboard aus SQLite laden fehlgeschlagen: {e}")
            return self.get_leaderboard(limit)

    # ------------------------------------------------------------------
    # Rank (SQLite-Query mit In-Memory-Fallback)
    # ------------------------------------------------------------------

    def get_rank(self, user_id: int) -> int:
        """
        Position eines Users im Leaderboard zurueckgeben.

        Verwendet In-Memory-Daten (aus SQLite geladen via load_from_db).

        Args:
            user_id: Discord-User-ID

        Returns:
            1-basierte Position (1 = erster Platz).
            Gibt len(data)+1 zurueck wenn User nicht gefunden.
        """
        # Alle User nach XP sortieren
        sorted_users = sorted(
            self._data.items(),
            key=lambda item: item[1].get("xp", 0),
            reverse=True,
        )

        uid = str(user_id)
        for index, (key, _) in enumerate(sorted_users, start=1):
            if key == uid:
                return index

        # User nicht in den Daten
        return len(sorted_users) + 1

    async def get_rank_db(self, user_id: int) -> int:
        """
        Position eines Users direkt aus SQLite abfragen (async Version).

        Verwendet COUNT WHERE xp > user_xp fuer effiziente Berechnung.
        Faellt auf In-Memory-Daten zurueck bei DB-Fehler.

        Args:
            user_id: Discord-User-ID

        Returns:
            1-basierte Position (1 = erster Platz).
            Gibt Gesamtzahl+1 zurueck wenn User nicht gefunden.
        """
        try:
            db = await get_db()
            uid = str(user_id)

            # Zuerst XP des Users holen
            cursor = await db.execute(
                "SELECT xp FROM leveling WHERE user_id = ?",
                (uid,),
            )
            row = await cursor.fetchone()

            if not row:
                # User nicht in der DB — Fallback
                cursor = await db.execute("SELECT COUNT(*) FROM leveling")
                count_row = await cursor.fetchone()
                total = count_row[0] if count_row else 0
                return total + 1

            user_xp = row["xp"] or 0

            # Anzahl der User mit mehr XP zaehlen
            cursor = await db.execute(
                "SELECT COUNT(*) FROM leveling WHERE xp > ?",
                (user_xp,),
            )
            count_row = await cursor.fetchone()
            rank = (count_row[0] if count_row else 0) + 1

            return rank

        except Exception as e:
            logger.error(f"Rang aus SQLite laden fehlgeschlagen: {e}")
            return self.get_rank(user_id)

    @property
    def config(self) -> dict[str, Any]:
        """Aktuelle Konfiguration zurueckgeben (Kopie)."""
        return dict(self._config)
