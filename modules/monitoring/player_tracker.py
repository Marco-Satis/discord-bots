"""
Player Tracker Module - Join/Leave Logging & Statistics
Tracks player sessions, playtime, and generates weekly reports

Persistenz: SQLite (players, player_sessions Tabellen) — alleinige Datenquelle
"""

import asyncio
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, Dict, List, Set, Any

from utils.logger import get_logger
from utils.async_tasks import schedule_from_sync
from modules.database.db_manager import get_db

logger = get_logger("player_tracker")


@dataclass
class PlayerSession:
    """A single play session"""
    join_time: str
    leave_time: Optional[str] = None
    duration_minutes: int = 0


@dataclass
class PlayerRecord:
    """Complete record for one player"""
    name: str
    first_seen: str
    last_seen: str
    total_playtime_minutes: int = 0
    session_count: int = 0
    sessions: list[dict] = field(default_factory=list)
    is_online: bool = False
    current_session_start: Optional[str] = None


class PlayerTracker:
    """
    Tracks player join/leave events and computes statistics.

    Usage:
        tracker = PlayerTracker()
        tracker.on_join = my_join_callback
        tracker.on_leave = my_leave_callback
        await tracker.load_from_db()  # Call from bot on_ready
        await tracker.update(current_player_names)
    """

    MAX_SESSIONS_PER_PLAYER = 100  # Keep last N sessions

    def __init__(self, data_dir: str = "data", server_type: str = "satisfactory",
                 **kwargs) -> None:
        self.server_type: str = server_type

        self.players: Dict[str, PlayerRecord] = {}
        self._online_players: Set[str] = set()

        # Callbacks: async def callback(player_name: str)
        self.on_join: Optional[Callable[[str], Awaitable]] = None
        self.on_leave: Optional[Callable[[str, int], Awaitable]] = None  # name, duration_min

    def _save(self) -> None:
        """No-op — alle Schreibvorgänge gehen direkt nach SQLite."""
        pass

    # ------------------------------------------------------------------
    # SQLite primary read — call from bot on_ready
    # ------------------------------------------------------------------

    async def load_from_db(self, server_type: Optional[str] = None) -> None:
        """
        Load players from SQLite into self.players.
        Call this from the bot's on_ready event.

        Args:
            server_type: Override server_type filter (default: self.server_type)
        """
        st = server_type or self.server_type
        try:
            db = await get_db()

            # Load all players for this server_type (or all if server_type is NULL)
            cursor = await db.execute(
                "SELECT id, name, first_seen, last_seen, total_playtime_seconds, server_type "
                "FROM players WHERE server_type = ? OR server_type IS NULL",
                (st,),
            )
            rows = await cursor.fetchall()

            if not rows:
                logger.info(
                    f"Keine Spieler in SQLite für server_type='{st}' — starte leer"
                )
                return

            # DB has data — replace in-memory dict
            db_players: Dict[str, PlayerRecord] = {}

            for row in rows:
                player_id = row[0]
                name = row[1]
                first_seen = row[2] or ""
                last_seen = row[3] or ""
                total_seconds = row[4] or 0
                total_minutes = total_seconds // 60

                # Load recent sessions for this player
                sess_cursor = await db.execute(
                    "SELECT join_time, leave_time, duration_seconds "
                    "FROM player_sessions "
                    "WHERE player_id = ? AND server_type = ? "
                    "ORDER BY join_time DESC LIMIT ?",
                    (player_id, st, self.MAX_SESSIONS_PER_PLAYER),
                )
                sess_rows = await sess_cursor.fetchall()

                sessions = []
                for sr in reversed(sess_rows):  # Reverse to chronological order
                    sessions.append({
                        "join": sr[0] or "",
                        "leave": sr[1] or "",
                        "duration_minutes": (sr[2] or 0) // 60,
                    })

                db_players[name] = PlayerRecord(
                    name=name,
                    first_seen=first_seen,
                    last_seen=last_seen,
                    total_playtime_minutes=total_minutes,
                    session_count=len(sessions),
                    sessions=sessions,
                    is_online=False,
                    current_session_start=None,
                )

            self.players = db_players
            logger.info(
                f"SQLite: {len(self.players)} Spieler-Datensaetze geladen "
                f"(server_type='{st}')"
            )

        except Exception as e:
            logger.warning(
                f"SQLite-Lesefehler fuer server_type='{st}': {e}"
            )

    # ------------------------------------------------------------------
    # SQLite write helpers
    # ------------------------------------------------------------------

    async def _db_upsert_player(self, record: PlayerRecord) -> None:
        """INSERT or UPDATE a player row in SQLite."""
        try:
            db = await get_db()
            await db.execute(
                """
                INSERT INTO players (name, first_seen, last_seen, total_playtime_seconds, server_type)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    total_playtime_seconds = excluded.total_playtime_seconds,
                    server_type = COALESCE(excluded.server_type, players.server_type)
                """,
                (
                    record.name,
                    record.first_seen,
                    record.last_seen,
                    record.total_playtime_minutes * 60,
                    self.server_type,
                ),
            )
            await db.commit()
        except Exception as e:
            logger.warning(f"SQLite upsert player '{record.name}' fehlgeschlagen: {e}")

    async def _db_insert_session(
        self, player_name: str, join_time: str, leave_time: str, duration_seconds: int
    ) -> None:
        """Insert a session row into player_sessions."""
        try:
            db = await get_db()

            # Resolve player_id
            cursor = await db.execute(
                "SELECT id FROM players WHERE name = ?", (player_name,)
            )
            row = await cursor.fetchone()
            if not row:
                logger.warning(
                    f"SQLite: Spieler '{player_name}' nicht in players-Tabelle — "
                    f"Session-Insert uebersprungen"
                )
                return
            player_id = row[0]

            await db.execute(
                """
                INSERT INTO player_sessions (player_id, server_type, join_time, leave_time, duration_seconds)
                VALUES (?, ?, ?, ?, ?)
                """,
                (player_id, self.server_type, join_time, leave_time, duration_seconds),
            )
            await db.commit()
        except Exception as e:
            logger.warning(
                f"SQLite insert session fuer '{player_name}' fehlgeschlagen: {e}"
            )

    async def _db_update_player_after_leave(
        self, record: PlayerRecord
    ) -> None:
        """Update last_seen and total_playtime_seconds after a leave event."""
        try:
            db = await get_db()
            await db.execute(
                """
                UPDATE players
                SET last_seen = ?,
                    total_playtime_seconds = ?
                WHERE name = ?
                """,
                (
                    record.last_seen,
                    record.total_playtime_minutes * 60,
                    record.name,
                ),
            )
            await db.commit()
        except Exception as e:
            logger.warning(
                f"SQLite update player '{record.name}' nach Leave fehlgeschlagen: {e}"
            )

    # ------------------------------------------------------------------
    # Core update loop
    # ------------------------------------------------------------------

    async def update(self, current_players: Set[str]) -> None:
        """
        Update tracker with current player set.
        Detects joins and leaves by comparing with previous state.
        """
        previous = self._online_players.copy()
        self._online_players = current_players

        # Detect joins
        joined = current_players - previous
        for name in joined:
            await self._handle_join(name)

        # Detect leaves
        left = previous - current_players
        for name in left:
            await self._handle_leave(name)

        if joined or left:
            self._save()  # no-op, kept for API compatibility

    async def _handle_join(self, name: str) -> None:
        """Handle player join event"""
        now = datetime.now().isoformat()
        logger.info(f"Player joined: {name}")

        if name not in self.players:
            self.players[name] = PlayerRecord(
                name=name,
                first_seen=now,
                last_seen=now,
            )

        record = self.players[name]
        record.is_online = True
        record.current_session_start = now
        record.last_seen = now

        # Persist to SQLite
        await self._db_upsert_player(record)

        if self.on_join:
            try:
                await self.on_join(name)
            except Exception as e:
                logger.error(f"Join callback error: {e}")

    async def _handle_leave(self, name: str) -> None:
        """Handle player leave event"""
        now = datetime.now()
        logger.info(f"Player left: {name}")

        duration_min = 0
        duration_sec = 0
        if name in self.players:
            record = self.players[name]
            record.is_online = False
            record.last_seen = now.isoformat()

            # Calculate session duration
            if record.current_session_start:
                try:
                    start = datetime.fromisoformat(record.current_session_start)
                    delta = now - start
                    duration_sec = int(delta.total_seconds())
                    duration_min = duration_sec // 60
                except (ValueError, TypeError):
                    duration_sec = 0
                    duration_min = 0

            # Record session in memory
            join_time_str = record.current_session_start or now.isoformat()
            session = {
                "join": join_time_str,
                "leave": now.isoformat(),
                "duration_minutes": duration_min,
            }
            record.sessions.append(session)
            record.session_count += 1
            record.total_playtime_minutes += duration_min
            record.current_session_start = None

            # Persist to SQLite: insert session + update player
            await self._db_insert_session(
                player_name=name,
                join_time=join_time_str,
                leave_time=now.isoformat(),
                duration_seconds=duration_sec,
            )
            await self._db_update_player_after_leave(record)

        if self.on_leave:
            try:
                await self.on_leave(name, duration_min)
            except Exception as e:
                logger.error(f"Leave callback error: {e}")

    # ------------------------------------------------------------------
    # Read-only stats helpers (unchanged API surface)
    # ------------------------------------------------------------------

    def get_online_players(self) -> Set[str]:
        """Get currently online players"""
        return self._online_players.copy()

    def get_player_stats(self, name: str) -> Optional[Dict[str, Any]]:
        """Get stats for a specific player"""
        record = self.players.get(name)
        if not record:
            return None

        hours = record.total_playtime_minutes / 60
        avg_session = (record.total_playtime_minutes / record.session_count
                       if record.session_count > 0 else 0)

        # Current session time
        current_session_min = 0
        if record.is_online and record.current_session_start:
            try:
                start = datetime.fromisoformat(record.current_session_start)
                current_session_min = int((datetime.now() - start).total_seconds() / 60)
            except (ValueError, TypeError):
                pass

        return {
            "name": record.name,
            "first_seen": record.first_seen,
            "last_seen": record.last_seen,
            "total_playtime_hours": round(hours, 1),
            "total_playtime_minutes": record.total_playtime_minutes,
            "session_count": record.session_count,
            "avg_session_minutes": round(avg_session),
            "is_online": record.is_online,
            "current_session_minutes": current_session_min,
            "recent_sessions": record.sessions[-5:],
        }

    def get_all_stats(self) -> List[Dict[str, Any]]:
        """Get stats for all players, sorted by playtime"""
        stats = []
        for name in self.players:
            s = self.get_player_stats(name)
            if s:
                stats.append(s)
        stats.sort(key=lambda x: x["total_playtime_minutes"], reverse=True)
        return stats

    def get_weekly_stats(self) -> Dict[str, Any]:
        """Get statistics for the past 7 days"""
        cutoff = datetime.now() - timedelta(days=7)
        cutoff_iso = cutoff.isoformat()

        active_players = {}
        total_sessions = 0
        total_playtime = 0

        for name, record in self.players.items():
            weekly_time = 0
            weekly_sessions = 0

            for session in record.sessions:
                if session.get("join", "") >= cutoff_iso:
                    weekly_sessions += 1
                    weekly_time += session.get("duration_minutes", 0)

            if weekly_sessions > 0:
                active_players[name] = {
                    "sessions": weekly_sessions,
                    "playtime_minutes": weekly_time,
                    "playtime_hours": round(weekly_time / 60, 1),
                }
                total_sessions += weekly_sessions
                total_playtime += weekly_time

        # Sort by playtime
        sorted_players = dict(
            sorted(active_players.items(),
                   key=lambda x: x[1]["playtime_minutes"], reverse=True)
        )

        return {
            "period_start": cutoff.strftime("%d.%m.%Y"),
            "period_end": datetime.now().strftime("%d.%m.%Y"),
            "active_player_count": len(active_players),
            "total_sessions": total_sessions,
            "total_playtime_hours": round(total_playtime / 60, 1),
            "players": sorted_players,
        }

    def format_weekly_report(self) -> str:
        """Generate formatted weekly report text"""
        stats = self.get_weekly_stats()

        lines = [
            f"\U0001f4ca **Wochenbericht** ({stats['period_start']} \u2013 {stats['period_end']})",
            "",
            f"**Aktive Spieler:** {stats['active_player_count']}",
            f"**Sessions gesamt:** {stats['total_sessions']}",
            f"**Spielzeit gesamt:** {stats['total_playtime_hours']}h",
            "",
        ]

        if stats["players"]:
            lines.append("**Spieler-Ranking:**")
            for i, (name, data) in enumerate(stats["players"].items(), 1):
                medal = "\U0001f947" if i == 1 else "\U0001f948" if i == 2 else "\U0001f949" if i == 3 else f"{i}."
                lines.append(
                    f"{medal} **{name}** \u2014 {data['playtime_hours']}h "
                    f"({data['sessions']} Sessions)"
                )
        else:
            lines.append("_Keine Spieleraktivitaet diese Woche._")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Shutdown — close all open sessions
    # ------------------------------------------------------------------

    def close_all_sessions(self) -> None:
        """Close all open sessions (e.g. on shutdown/crash).
        Writes session data to SQLite via fire-and-forget asyncio task."""
        now = datetime.now()
        closed = 0
        sessions_to_persist: list[dict] = []

        for name, record in self.players.items():
            if record.is_online:
                duration_min = 0
                duration_sec = 0
                if record.current_session_start:
                    try:
                        start = datetime.fromisoformat(record.current_session_start)
                        delta = now - start
                        duration_sec = int(delta.total_seconds())
                        duration_min = duration_sec // 60
                    except (ValueError, TypeError):
                        pass

                join_time_str = record.current_session_start or now.isoformat()
                session = {
                    "join": join_time_str,
                    "leave": now.isoformat(),
                    "duration_minutes": duration_min,
                    "closed_reason": "tracker_shutdown",
                }
                record.sessions.append(session)
                record.session_count += 1
                record.total_playtime_minutes += duration_min
                record.is_online = False
                record.current_session_start = None
                closed += 1

                # Collect data for async persistence
                sessions_to_persist.append({
                    "name": name,
                    "join_time": join_time_str,
                    "leave_time": now.isoformat(),
                    "duration_seconds": duration_sec,
                    "total_playtime_minutes": record.total_playtime_minutes,
                    "last_seen": record.last_seen,
                })

        # Online-Set leeren (das fehlte — verursachte Endlos-Spam)
        self._online_players.clear()

        if closed > 0:
            self._save()  # no-op, kept for API compatibility
            logger.info(f"{closed} Stale-Sessions geschlossen")

            # Persist closed sessions to SQLite mit Reference-Tracking
            # (audit-fix 2026-05-17, async.md H2 — Fire-and-Forget verlor Tasks an GC)
            if sessions_to_persist:
                task = schedule_from_sync(
                    self._persist_closed_sessions(sessions_to_persist),
                    name=f"player_tracker.persist[{len(sessions_to_persist)}]",
                )
                if task is None:
                    logger.warning(
                        f"Event-Loop nicht verfuegbar — SQLite-Persist fuer "
                        f"{len(sessions_to_persist)} Sessions uebersprungen"
                    )

    async def _persist_closed_sessions(self, sessions: list[dict]) -> None:
        """Persist batch of closed sessions to SQLite (called from fire-and-forget task)."""
        try:
            db = await get_db()

            for s in sessions:
                # Update player record
                try:
                    await db.execute(
                        """
                        UPDATE players
                        SET last_seen = ?,
                            total_playtime_seconds = ?
                        WHERE name = ?
                        """,
                        (
                            s["last_seen"],
                            s["total_playtime_minutes"] * 60,
                            s["name"],
                        ),
                    )
                except Exception as e:
                    logger.warning(f"SQLite update player '{s['name']}' bei Shutdown fehlgeschlagen: {e}")

                # Insert session
                try:
                    cursor = await db.execute(
                        "SELECT id FROM players WHERE name = ?", (s["name"],)
                    )
                    row = await cursor.fetchone()
                    if row:
                        await db.execute(
                            """
                            INSERT INTO player_sessions
                                (player_id, server_type, join_time, leave_time, duration_seconds)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (row[0], self.server_type, s["join_time"], s["leave_time"], s["duration_seconds"]),
                        )
                except Exception as e:
                    logger.warning(f"SQLite insert session '{s['name']}' bei Shutdown fehlgeschlagen: {e}")

            await db.commit()
            logger.info(f"SQLite: {len(sessions)} Shutdown-Sessions persistiert")

        except Exception as e:
            logger.warning(f"SQLite Shutdown-Persist fehlgeschlagen: {e}")
