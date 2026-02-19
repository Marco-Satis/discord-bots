"""
Player Tracker Module - Join/Leave Logging & Statistics
Tracks player sessions, playtime, and generates weekly reports
"""

import json
import asyncio
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, Awaitable, Dict, List, Set, Any

from utils.logger import get_logger

logger = get_logger("player_tracker")

DATA_FILE = "data/player_stats.json"


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
        await tracker.update(current_player_names)
    """

    MAX_SESSIONS_PER_PLAYER = 100  # Keep last N sessions

    def __init__(self, data_dir: str = "data") -> None:
        self.data_dir: Path = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_file: Path = self.data_dir / "player_stats.json"

        self.players: Dict[str, PlayerRecord] = {}
        self._online_players: Set[str] = set()
        self._load()

        # Callbacks: async def callback(player_name: str)
        self.on_join: Optional[Callable[[str], Awaitable]] = None
        self.on_leave: Optional[Callable[[str, int], Awaitable]] = None  # name, duration_min

    def _load(self) -> None:
        """Load player data from JSON"""
        if self.data_file.exists():
            try:
                with open(self.data_file, "r") as f:
                    data = json.load(f)
                for name, info in data.items():
                    self.players[name] = PlayerRecord(
                        name=info["name"],
                        first_seen=info["first_seen"],
                        last_seen=info["last_seen"],
                        total_playtime_minutes=info.get("total_playtime_minutes", 0),
                        session_count=info.get("session_count", 0),
                        sessions=info.get("sessions", [])[-self.MAX_SESSIONS_PER_PLAYER:],
                        is_online=False,  # Reset on load
                        current_session_start=None,
                    )
                logger.info(f"Loaded {len(self.players)} player records")
            except (json.JSONDecodeError, OSError) as e:
                logger.error(f"Failed to load player stats: {e}")

    def _save(self) -> None:
        """Save player data to JSON"""
        try:
            data = {}
            for name, record in self.players.items():
                data[name] = {
                    "name": record.name,
                    "first_seen": record.first_seen,
                    "last_seen": record.last_seen,
                    "total_playtime_minutes": record.total_playtime_minutes,
                    "session_count": record.session_count,
                    "sessions": record.sessions[-self.MAX_SESSIONS_PER_PLAYER:],
                }
            with open(self.data_file, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error(f"Failed to save player stats: {e}")

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
            self._save()

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
        if name in self.players:
            record = self.players[name]
            record.is_online = False
            record.last_seen = now.isoformat()

            # Calculate session duration
            if record.current_session_start:
                try:
                    start = datetime.fromisoformat(record.current_session_start)
                    duration_min = int((now - start).total_seconds() / 60)
                except (ValueError, TypeError):
                    duration_min = 0

            # Record session
            session = {
                "join": record.current_session_start or now.isoformat(),
                "leave": now.isoformat(),
                "duration_minutes": duration_min,
            }
            record.sessions.append(session)
            record.session_count += 1
            record.total_playtime_minutes += duration_min
            record.current_session_start = None

        if self.on_leave:
            try:
                await self.on_leave(name, duration_min)
            except Exception as e:
                logger.error(f"Leave callback error: {e}")

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
            f"📊 **Wochenbericht** ({stats['period_start']} – {stats['period_end']})",
            "",
            f"**Aktive Spieler:** {stats['active_player_count']}",
            f"**Sessions gesamt:** {stats['total_sessions']}",
            f"**Spielzeit gesamt:** {stats['total_playtime_hours']}h",
            "",
        ]

        if stats["players"]:
            lines.append("**Spieler-Ranking:**")
            for i, (name, data) in enumerate(stats["players"].items(), 1):
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                lines.append(
                    f"{medal} **{name}** — {data['playtime_hours']}h "
                    f"({data['sessions']} Sessions)"
                )
        else:
            lines.append("_Keine Spieleraktivitaet diese Woche._")

        return "\n".join(lines)

    def close_all_sessions(self) -> None:
        """Close all open sessions (e.g. on shutdown/crash)"""
        now = datetime.now()
        for name, record in self.players.items():
            if record.is_online:
                duration_min = 0
                if record.current_session_start:
                    try:
                        start = datetime.fromisoformat(record.current_session_start)
                        duration_min = int((now - start).total_seconds() / 60)
                    except (ValueError, TypeError):
                        pass

                session = {
                    "join": record.current_session_start or now.isoformat(),
                    "leave": now.isoformat(),
                    "duration_minutes": duration_min,
                    "closed_reason": "tracker_shutdown",
                }
                record.sessions.append(session)
                record.session_count += 1
                record.total_playtime_minutes += duration_min
                record.is_online = False
                record.current_session_start = None

        self._save()
