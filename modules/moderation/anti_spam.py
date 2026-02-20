"""
Discord Anti-Spam — Spam-Erkennung fuer Discord-Nachrichten (Admin Bot)

Erkennt und blockiert:
- Message-Flooding (zu viele Nachrichten in kurzem Zeitfenster)
- Doppelte Nachrichten (identischer Inhalt wiederholt)
- Mention-Spam (mehr als 5 Mentions in einer Nachricht)

NICHT fuer RCON/In-Game — dafuer existiert modules/anti_spam.py
"""

import time
from collections import defaultdict
from typing import Optional

from utils.logger import get_logger

logger = get_logger("moderation.anti_spam")

# Schwellwert fuer Mention-Spam
MAX_MENTIONS = 5

# Maximale Anzahl gespeicherter Nachrichten pro User (fuer Duplikat-Erkennung)
MAX_HISTORY_PER_USER = 10

# Mindestanzahl identischer Nachrichten fuer Duplikat-Erkennung
DUPLICATE_THRESHOLD = 3


class DiscordAntiSpam:
    """
    Spam-Erkennung fuer Discord-Nachrichten im Admin Bot.

    Trackt Nachrichten pro User und erkennt:
    - Rate-Limit-Ueberschreitung (max_messages in window_seconds)
    - Wiederholte identische Nachrichten
    - Mention-Spam (>5 Mentions)

    Nach Erkennung wird ein Cooldown gesetzt, waehrend dessen alle
    Nachrichten des Users blockiert werden.
    """

    def __init__(
        self,
        max_messages: int = 5,
        window_seconds: int = 10,
        cooldown_seconds: int = 30,
    ) -> None:
        """
        Args:
            max_messages: Maximale Nachrichten pro Zeitfenster bevor Spam erkannt wird
            window_seconds: Laenge des Zeitfensters in Sekunden
            cooldown_seconds: Cooldown-Dauer in Sekunden nach Spam-Erkennung
        """
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds

        # Tracking-Daten pro User
        self._timestamps: dict[int, list[float]] = defaultdict(list)
        self._cooldowns: dict[int, float] = {}  # user_id -> cooldown_until
        self._last_messages: dict[int, list[str]] = defaultdict(list)

        self.enabled: bool = True

    # ------------------------------------------------------------------
    # Nachrichtenpruefung
    # ------------------------------------------------------------------

    def check_message(
        self,
        user_id: int,
        content: str = "",
        mention_count: int = 0,
    ) -> tuple[bool, str | None]:
        """
        Prueft ob eine Nachricht als Spam eingestuft wird.

        Args:
            user_id: Discord User-ID
            content: Nachrichteninhalt (fuer Duplikat-Erkennung)
            mention_count: Anzahl der Mentions in der Nachricht

        Returns:
            (is_spam, reason) — True wenn Spam erkannt, mit Begruendung
        """
        if not self.enabled:
            return False, None

        now = time.time()

        # --- Cooldown pruefen ---
        if user_id in self._cooldowns:
            if now < self._cooldowns[user_id]:
                remaining = int(self._cooldowns[user_id] - now)
                return True, f"Cooldown aktiv: noch {remaining}s"
            else:
                # Cooldown abgelaufen — aufraemen
                del self._cooldowns[user_id]

        # --- Mention-Spam pruefen ---
        if mention_count > MAX_MENTIONS:
            self._cooldowns[user_id] = now + self.cooldown_seconds
            logger.warning(
                f"Mention-Spam von User {user_id}: "
                f"{mention_count} Mentions in einer Nachricht"
            )
            return True, (
                f"Zu viele Mentions ({mention_count})! "
                f"{self.cooldown_seconds}s Cooldown."
            )

        # --- Alte Timestamps bereinigen ---
        self._timestamps[user_id] = [
            t for t in self._timestamps[user_id]
            if now - t < self.window_seconds
        ]

        # --- Rate-Limit pruefen (VOR dem Hinzufuegen) ---
        if len(self._timestamps[user_id]) >= self.max_messages:
            self._cooldowns[user_id] = now + self.cooldown_seconds
            logger.warning(
                f"Message-Flooding von User {user_id}: "
                f"{len(self._timestamps[user_id])} Nachrichten "
                f"in {self.window_seconds}s"
            )
            return True, (
                f"Zu viele Nachrichten! {self.cooldown_seconds}s Cooldown."
            )

        # Timestamp eintragen
        self._timestamps[user_id].append(now)

        # --- Duplikat-Erkennung ---
        if content:
            content_lower = content.strip().lower()
            self._last_messages[user_id].append(content_lower)
            # Nur die letzten N Nachrichten behalten
            self._last_messages[user_id] = (
                self._last_messages[user_id][-MAX_HISTORY_PER_USER:]
            )

            recent = self._last_messages[user_id][-DUPLICATE_THRESHOLD:]
            if (
                len(recent) >= DUPLICATE_THRESHOLD
                and all(m == content_lower for m in recent)
            ):
                self._cooldowns[user_id] = now + self.cooldown_seconds
                logger.warning(
                    f"Doppelte Nachrichten von User {user_id}: "
                    f"'{content_lower[:50]}' {DUPLICATE_THRESHOLD}x wiederholt"
                )
                return True, "Doppelte Nachrichten erkannt! Cooldown."

        return False, None

    # ------------------------------------------------------------------
    # Verwaltung
    # ------------------------------------------------------------------

    def is_on_cooldown(self, user_id: int) -> bool:
        """Prueft ob ein User aktuell auf Cooldown ist"""
        if user_id in self._cooldowns:
            if time.time() < self._cooldowns[user_id]:
                return True
            else:
                del self._cooldowns[user_id]
        return False

    def get_cooldown_remaining(self, user_id: int) -> int:
        """Verbleibende Cooldown-Sekunden fuer einen User"""
        if user_id in self._cooldowns:
            remaining = self._cooldowns[user_id] - time.time()
            return max(0, int(remaining))
        return 0

    def reset_user(self, user_id: int) -> None:
        """Alle Tracking-Daten fuer einen User zuruecksetzen"""
        self._timestamps.pop(user_id, None)
        self._cooldowns.pop(user_id, None)
        self._last_messages.pop(user_id, None)

    def cleanup(self, inactive_threshold: int = 3600) -> None:
        """
        Abgelaufene Tracking-Daten und inaktive User entfernen.

        Args:
            inactive_threshold: Sekunden ohne Aktivitaet bevor User-Daten geloescht werden
        """
        now = time.time()

        # Abgelaufene Cooldowns entfernen
        expired_cooldowns = [
            uid for uid, until in self._cooldowns.items()
            if until < now
        ]
        for uid in expired_cooldowns:
            del self._cooldowns[uid]

        # Alte Timestamp-Daten entfernen
        for uid in list(self._timestamps.keys()):
            self._timestamps[uid] = [
                t for t in self._timestamps[uid]
                if now - t < self.window_seconds * 2
            ]
            if not self._timestamps[uid]:
                del self._timestamps[uid]

        # Inaktive User aus der Nachrichtenhistorie entfernen
        for uid in list(self._last_messages.keys()):
            if uid not in self._timestamps and uid not in self._cooldowns:
                del self._last_messages[uid]

    def get_stats(self) -> dict[str, int | bool]:
        """Anti-Spam Statistiken zurueckgeben"""
        now = time.time()
        active_cooldowns = sum(
            1 for t in self._cooldowns.values() if t > now
        )
        return {
            "enabled": self.enabled,
            "tracked_users": len(self._timestamps),
            "active_cooldowns": active_cooldowns,
        }
