"""
Voice-Session-Tracker — Anti-Cheat-faire Voice-XP (Phase C, C3).

Ersetzt das grobe 5-Minuten-Tick-Modell durch echte Sessions mit
Gueltigkeits-Akkumulation:

  - join  -> `start()` oeffnet eine Session (DB-Row in `voice_sessions`, v6).
  - Ein periodischer Sampler (60 s, im Cog) ruft `sample(valid=...)` je offener
    Session: nur *valide* Sekunden werden akkumuliert. Valid = ≥2 menschliche
    Teilnehmer, kein self-deafen/deafen, nicht im AFK-Channel (Bedingungen
    rechnet das Cog aus dem Discord-VoiceState aus — dieses Modul bleibt
    discord-frei und damit unit-testbar).
  - leave -> `end()` schliesst die Session: Dauer + valide Minuten + valid-Flag
    (Session zaehlt nur ab `min_seconds` valider Zeit). XP basiert auf den
    validen Minuten (alleine/deaf die ganze Zeit => 0 valide Minuten => 0 XP).

Daten sind guild-scoped (Spalte `guild_id`), kein Cross-Guild-Leak.

Persistenz-Tabelle `voice_sessions` (Migration v6):
  id, guild_id TEXT, user_id TEXT, channel_id TEXT,
  join_time, leave_time, duration_seconds, xp_awarded, valid
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from utils.logger import get_logger
from modules.database.db_manager import get_db, DBHelper, transaction

logger = get_logger("voice_sessions")


def _iso(ts: float) -> str:
    """Epoch-Sekunden -> ISO-8601 (UTC)."""
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class VoiceSessionTracker:
    """
    Verwaltet offene Voice-Sessions im Speicher + persistiert sie in SQLite.

    Discord-frei: das Cog reicht primitive Werte (IDs, bool, Zeitstempel) rein.
    Zeitstempel sind optional injizierbar (`now`) fuer deterministische Tests.
    """

    def __init__(self) -> None:
        # (guild_id, user_id) -> {channel_id, join, last, valid_seconds, db_id}
        self._open: dict[tuple[str, str], dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Abfragen
    # ------------------------------------------------------------------

    def is_open(self, guild_id: int | str, user_id: int | str) -> bool:
        """True wenn fuer (guild, user) eine Session offen ist."""
        return (str(guild_id), str(user_id)) in self._open

    def open_keys(self) -> list[tuple[str, str]]:
        """Snapshot aller offenen (guild_id, user_id)-Schluessel."""
        return list(self._open.keys())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        guild_id: int | str,
        user_id: int | str,
        channel_id: int | str,
        now: Optional[float] = None,
    ) -> None:
        """
        Oeffnet eine Session (idempotent: laeuft schon eine, passiert nichts).

        Legt eine offene DB-Row an (leave_time NULL).
        """
        now = time.time() if now is None else now
        key = (str(guild_id), str(user_id))
        if key in self._open:
            return  # bereits offen — idempotent

        # Platzhalter VOR dem INSERT-await reservieren, damit der Idempotenz-Guard
        # auch im await-Fenster greift (kein doppelter INSERT/Waisen-Row bei
        # schnellem Re-Event, z.B. join+move binnen Millisekunden).
        self._open[key] = {
            "channel_id": str(channel_id),
            "join": now,
            "last": now,
            "valid_seconds": 0.0,
            "db_id": None,
        }

        try:
            db = DBHelper(await get_db())
            new_id = await db.execute(  # commit + Write-Retry (busy/locked)
                "INSERT INTO voice_sessions (guild_id, user_id, channel_id, join_time) "
                "VALUES (?, ?, ?, ?)",
                (str(guild_id), str(user_id), str(channel_id), _iso(now)),
            )
            # Session koennte zwischenzeitlich beendet worden sein
            if key in self._open:
                self._open[key]["db_id"] = new_id
        except Exception as e:
            logger.error(f"Voice-Session start fuer {key} fehlgeschlagen: {e}")

    def sample(
        self,
        guild_id: int | str,
        user_id: int | str,
        valid: bool,
        now: Optional[float] = None,
    ) -> None:
        """
        Periodischer Sample-Punkt: akkumuliert die seit dem letzten Sample
        vergangene Zeit NUR wenn `valid`. Aktualisiert immer den last-Stempel.
        """
        now = time.time() if now is None else now
        s = self._open.get((str(guild_id), str(user_id)))
        if s is None:
            return
        if valid:
            s["valid_seconds"] += now - s["last"]
        s["last"] = now

    async def end(
        self,
        guild_id: int | str,
        user_id: int | str,
        *,
        valid_final: bool = False,
        min_seconds: int = 60,
        now: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Schliesst eine Session. Das letzte Teilsegment (seit dem letzten Sample)
        wird standardmaessig NICHT mitgezaehlt (`valid_final=False`) — es ist
        kurz (<Sample-Intervall) und seine Gueltigkeit beim Verlassen mehrdeutig.

        Returns:
            None wenn keine Session offen war, sonst Dict mit
            db_id, duration (s), valid_minutes, valid (bool).
        """
        now = time.time() if now is None else now
        key = (str(guild_id), str(user_id))
        s = self._open.pop(key, None)
        if s is None:
            return None

        if valid_final:
            s["valid_seconds"] += now - s["last"]

        duration = max(0, int(now - s["join"]))
        valid_minutes = int(s["valid_seconds"] // 60)
        is_valid = 1 if s["valid_seconds"] >= min_seconds else 0

        if s["db_id"] is not None:
            try:
                await DBHelper(await get_db()).execute(  # commit + Write-Retry
                    "UPDATE voice_sessions "
                    "SET leave_time = ?, duration_seconds = ?, valid = ? "
                    "WHERE id = ?",
                    (_iso(now), duration, is_valid, s["db_id"]),
                )
            except Exception as e:
                logger.error(f"Voice-Session end fuer {key} fehlgeschlagen: {e}")

        return {
            "db_id": s["db_id"],
            "duration": duration,
            "valid_minutes": valid_minutes,
            "valid": bool(is_valid),
        }

    async def set_xp(self, db_id: Optional[int], xp: int) -> None:
        """Vergebene XP an einer (geschlossenen) Session-Row vermerken (Audit)."""
        if db_id is None:
            return
        try:
            await DBHelper(await get_db()).execute(  # commit + Write-Retry
                "UPDATE voice_sessions SET xp_awarded = ? WHERE id = ?",
                (int(xp), db_id),
            )
        except Exception as e:
            logger.error(f"Voice-Session set_xp fuer {db_id} fehlgeschlagen: {e}")

    async def close_orphans(self, now: Optional[float] = None) -> int:
        """
        Verwaiste offene Sessions schliessen (z.B. nach Bot-Neustart) —
        leave_time NULL -> jetzt, valid=0. Verhindert unbegrenzt offene Rows.

        Returns:
            Anzahl geschlossener Rows.
        """
        now = time.time() if now is None else now
        try:
            async with transaction() as conn:  # BEGIN IMMEDIATE + Retry (cross-Prozess), rowcount
                cursor = await conn.execute(
                    "UPDATE voice_sessions SET leave_time = ?, valid = 0 "
                    "WHERE leave_time IS NULL",
                    (_iso(now),),
                )
                closed = cursor.rowcount or 0
            if closed:
                logger.info(f"{closed} verwaiste Voice-Session(s) geschlossen")
            return closed
        except Exception as e:
            logger.error(f"close_orphans fehlgeschlagen: {e}")
            return 0
