"""
Raid-Detector — Join-Spike-Erkennung (Phase G Security).

Reine, discord-freie Logik (unit-testbar): zaehlt Joins pro Guild in einem
Gleitfenster. Ueberschreitet die Anzahl in `window_seconds` die `threshold`,
gilt es als Raid -> der Cog alarmiert / sperrt.

Zeitstempel werden injiziert (`now`), damit Tests deterministisch sind.
Per-Guild isoliert (eigene Deque je Guild).
"""
from __future__ import annotations

from collections import deque


class RaidDetector:
    """Gleitfenster-Zaehler fuer Member-Joins je Guild."""

    def __init__(self) -> None:
        # guild_id -> deque[float] (Join-Zeitstempel, aufsteigend)
        self._joins: dict[str, deque[float]] = {}
        # guild_id -> letzter Alarm-Zeitstempel (Alarm-Cooldown gegen Spam)
        self._last_alert: dict[str, float] = {}

    def record_join(
        self,
        guild_id: int | str,
        now: float,
        *,
        threshold: int,
        window_seconds: float,
    ) -> bool:
        """
        Einen Join registrieren. Gibt True zurueck wenn die Join-Anzahl im
        Fenster `threshold` erreicht/ueberschreitet (Raid-Verdacht).

        threshold <= 0 deaktiviert die Erkennung (immer False).
        """
        if threshold <= 0:
            return False
        gid = str(guild_id)
        dq = self._joins.setdefault(gid, deque())
        dq.append(now)
        cutoff = now - window_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()
        return len(dq) >= threshold

    def join_count(self, guild_id: int | str) -> int:
        """Aktuelle Anzahl Joins im (zuletzt geprunten) Fenster."""
        return len(self._joins.get(str(guild_id), ()))

    def should_alert(
        self, guild_id: int | str, now: float, *, alert_cooldown: float = 60.0
    ) -> bool:
        """
        Ob ein Alarm gesendet werden soll (Alarm-Cooldown gegen Mehrfach-Pings
        waehrend desselben Raids). Markiert bei True den Alarm-Zeitpunkt.
        """
        gid = str(guild_id)
        last = self._last_alert.get(gid)
        if last is not None and (now - last) < alert_cooldown:
            return False
        self._last_alert[gid] = now
        return True

    def reset(self, guild_id: int | str) -> None:
        """Zaehler + Alarm-State einer Guild zuruecksetzen."""
        gid = str(guild_id)
        self._joins.pop(gid, None)
        self._last_alert.pop(gid, None)
