"""
F28: Datenbank-Models — Dataclasses fuer typisierte Abfragen

Jede Dataclass entspricht einer SQLite-Tabelle und bietet
from_row() fuer die Konvertierung von aiosqlite.Row Objekten.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any


def _get(row: Any, key: str, default: Any = None) -> Any:
    """Sicherer Zugriff auf Row-Felder (aiosqlite.Row oder dict)."""
    try:
        return row[key] if row[key] is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def _parse_dt(val: Any) -> Optional[datetime]:
    """Parst einen Timestamp-String zu datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except (ValueError, TypeError):
        return None


@dataclass
class Player:
    id: int = 0
    name: str = ""
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    total_playtime_seconds: int = 0
    server_type: Optional[str] = None
    discord_id: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Any) -> "Player":
        return cls(
            id=_get(row, "id", 0),
            name=_get(row, "name", ""),
            first_seen=_parse_dt(_get(row, "first_seen")),
            last_seen=_parse_dt(_get(row, "last_seen")),
            total_playtime_seconds=_get(row, "total_playtime_seconds", 0),
            server_type=_get(row, "server_type"),
            discord_id=_get(row, "discord_id"),
            created_at=_parse_dt(_get(row, "created_at")),
        )


@dataclass
class PlayerSession:
    id: int = 0
    player_id: int = 0
    server_type: str = ""
    join_time: Optional[datetime] = None
    leave_time: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    ip_address: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "PlayerSession":
        return cls(
            id=_get(row, "id", 0),
            player_id=_get(row, "player_id", 0),
            server_type=_get(row, "server_type", ""),
            join_time=_parse_dt(_get(row, "join_time")),
            leave_time=_parse_dt(_get(row, "leave_time")),
            duration_seconds=_get(row, "duration_seconds"),
            ip_address=_get(row, "ip_address"),
        )


@dataclass
class PlayerIP:
    id: int = 0
    player_id: int = 0
    ip_address: str = ""
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    server_type: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "PlayerIP":
        return cls(
            id=_get(row, "id", 0),
            player_id=_get(row, "player_id", 0),
            ip_address=_get(row, "ip_address", ""),
            first_seen=_parse_dt(_get(row, "first_seen")),
            last_seen=_parse_dt(_get(row, "last_seen")),
            server_type=_get(row, "server_type"),
        )


@dataclass
class StatsEntry:
    id: int = 0
    timestamp: Optional[datetime] = None
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None
    ram_used_gb: Optional[float] = None
    disk_percent: Optional[float] = None
    disk_used_gb: Optional[float] = None
    server_data: Optional[str] = None  # JSON-Blob

    @classmethod
    def from_row(cls, row: Any) -> "StatsEntry":
        return cls(
            id=_get(row, "id", 0),
            timestamp=_parse_dt(_get(row, "timestamp")),
            cpu_percent=_get(row, "cpu_percent"),
            ram_percent=_get(row, "ram_percent"),
            ram_used_gb=_get(row, "ram_used_gb"),
            disk_percent=_get(row, "disk_percent"),
            disk_used_gb=_get(row, "disk_used_gb"),
            server_data=_get(row, "server_data"),
        )


@dataclass
class Event:
    id: int = 0
    timestamp: Optional[datetime] = None
    event_type: str = ""
    category: Optional[str] = None
    server_id: Optional[str] = None
    message: str = ""
    details: Optional[str] = None  # JSON

    @classmethod
    def from_row(cls, row: Any) -> "Event":
        return cls(
            id=_get(row, "id", 0),
            timestamp=_parse_dt(_get(row, "timestamp")),
            event_type=_get(row, "event_type", ""),
            category=_get(row, "category"),
            server_id=_get(row, "server_id"),
            message=_get(row, "message", ""),
            details=_get(row, "details"),
        )


@dataclass
class Warn:
    id: int = 0
    user_id: str = ""
    moderator_id: str = ""
    reason: str = ""
    points: int = 1
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    active: bool = True

    @classmethod
    def from_row(cls, row: Any) -> "Warn":
        return cls(
            id=_get(row, "id", 0),
            user_id=_get(row, "user_id", ""),
            moderator_id=_get(row, "moderator_id", ""),
            reason=_get(row, "reason", ""),
            points=_get(row, "points", 1),
            created_at=_parse_dt(_get(row, "created_at")),
            expires_at=_parse_dt(_get(row, "expires_at")),
            active=bool(_get(row, "active", True)),
        )


@dataclass
class Ticket:
    id: int = 0
    channel_id: Optional[str] = None
    user_id: str = ""
    subject: Optional[str] = None
    status: str = "open"
    created_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "Ticket":
        return cls(
            id=_get(row, "id", 0),
            channel_id=_get(row, "channel_id"),
            user_id=_get(row, "user_id", ""),
            subject=_get(row, "subject"),
            status=_get(row, "status", "open"),
            created_at=_parse_dt(_get(row, "created_at")),
            closed_at=_parse_dt(_get(row, "closed_at")),
            closed_by=_get(row, "closed_by"),
        )


@dataclass
class TicketMessage:
    id: int = 0
    ticket_id: int = 0
    user_id: str = ""
    content: str = ""
    timestamp: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Any) -> "TicketMessage":
        return cls(
            id=_get(row, "id", 0),
            ticket_id=_get(row, "ticket_id", 0),
            user_id=_get(row, "user_id", ""),
            content=_get(row, "content", ""),
            timestamp=_parse_dt(_get(row, "timestamp")),
        )


@dataclass
class LevelingUser:
    id: int = 0
    user_id: str = ""
    xp: int = 0
    level: int = 0
    messages: int = 0
    voice_minutes: int = 0
    last_xp_time: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Any) -> "LevelingUser":
        return cls(
            id=_get(row, "id", 0),
            user_id=_get(row, "user_id", ""),
            xp=_get(row, "xp", 0),
            level=_get(row, "level", 0),
            messages=_get(row, "messages", 0),
            voice_minutes=_get(row, "voice_minutes", 0),
            last_xp_time=_parse_dt(_get(row, "last_xp_time")),
        )


@dataclass
class Giveaway:
    id: int = 0
    message_id: Optional[str] = None
    channel_id: Optional[str] = None
    guild_id: Optional[str] = None
    prize: str = ""
    winner_count: int = 1
    ends_at: Optional[datetime] = None
    ended: bool = False
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Any) -> "Giveaway":
        return cls(
            id=_get(row, "id", 0),
            message_id=_get(row, "message_id"),
            channel_id=_get(row, "channel_id"),
            guild_id=_get(row, "guild_id"),
            prize=_get(row, "prize", ""),
            winner_count=_get(row, "winner_count", 1),
            ends_at=_parse_dt(_get(row, "ends_at")),
            ended=bool(_get(row, "ended", False)),
            created_at=_parse_dt(_get(row, "created_at")),
        )


@dataclass
class Ban:
    id: int = 0
    ip_address: str = ""
    player_name: Optional[str] = None
    server_type: Optional[str] = None
    reason: Optional[str] = None
    banned_by: Optional[str] = None
    banned_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    active: bool = True
    source: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "Ban":
        return cls(
            id=_get(row, "id", 0),
            ip_address=_get(row, "ip_address", ""),
            player_name=_get(row, "player_name"),
            server_type=_get(row, "server_type"),
            reason=_get(row, "reason"),
            banned_by=_get(row, "banned_by"),
            banned_at=_parse_dt(_get(row, "banned_at")),
            expires_at=_parse_dt(_get(row, "expires_at")),
            active=bool(_get(row, "active", True)),
            source=_get(row, "source"),
        )


@dataclass
class BackupRecord:
    id: int = 0
    server_type: str = ""
    backup_type: Optional[str] = None
    filename: str = ""
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    integrity_ok: Optional[bool] = None
    cloud_synced: bool = False
    cloud_synced_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Any) -> "BackupRecord":
        return cls(
            id=_get(row, "id", 0),
            server_type=_get(row, "server_type", ""),
            backup_type=_get(row, "backup_type"),
            filename=_get(row, "filename", ""),
            size_bytes=_get(row, "size_bytes"),
            checksum=_get(row, "checksum"),
            integrity_ok=_get(row, "integrity_ok"),
            cloud_synced=bool(_get(row, "cloud_synced", False)),
            cloud_synced_at=_parse_dt(_get(row, "cloud_synced_at")),
            created_at=_parse_dt(_get(row, "created_at")),
        )


@dataclass
class AuditEntry:
    id: int = 0
    timestamp: Optional[datetime] = None
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    action: str = ""
    target: Optional[str] = None
    details: Optional[str] = None  # JSON
    source: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "AuditEntry":
        return cls(
            id=_get(row, "id", 0),
            timestamp=_parse_dt(_get(row, "timestamp")),
            user_id=_get(row, "user_id"),
            user_name=_get(row, "user_name"),
            action=_get(row, "action", ""),
            target=_get(row, "target"),
            details=_get(row, "details"),
            source=_get(row, "source"),
        )


@dataclass
class CommandEntry:
    id: int = 0
    timestamp: Optional[datetime] = None
    command_name: str = ""
    user_id: str = ""
    user_name: Optional[str] = None
    guild_id: Optional[str] = None
    channel_id: Optional[str] = None
    success: bool = True
    error_message: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "CommandEntry":
        return cls(
            id=_get(row, "id", 0),
            timestamp=_parse_dt(_get(row, "timestamp")),
            command_name=_get(row, "command_name", ""),
            user_id=_get(row, "user_id", ""),
            user_name=_get(row, "user_name"),
            guild_id=_get(row, "guild_id"),
            channel_id=_get(row, "channel_id"),
            success=bool(_get(row, "success", True)),
            error_message=_get(row, "error_message"),
        )


@dataclass
class WhitelistEntry:
    id: int = 0
    player_name: str = ""
    server_type: str = ""
    added_by: Optional[str] = None
    added_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Any) -> "WhitelistEntry":
        return cls(
            id=_get(row, "id", 0),
            player_name=_get(row, "player_name", ""),
            server_type=_get(row, "server_type", ""),
            added_by=_get(row, "added_by"),
            added_at=_parse_dt(_get(row, "added_at")),
        )


@dataclass
class BlacklistEntry:
    id: int = 0
    player_name: Optional[str] = None
    ip_address: Optional[str] = None
    server_type: str = ""
    reason: Optional[str] = None
    added_by: Optional[str] = None
    added_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Any) -> "BlacklistEntry":
        return cls(
            id=_get(row, "id", 0),
            player_name=_get(row, "player_name"),
            ip_address=_get(row, "ip_address"),
            server_type=_get(row, "server_type", ""),
            reason=_get(row, "reason"),
            added_by=_get(row, "added_by"),
            added_at=_parse_dt(_get(row, "added_at")),
        )


@dataclass
class ScheduledTask:
    id: int = 0
    task_name: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    success: Optional[bool] = None
    duration_seconds: Optional[float] = None
    details: Optional[str] = None  # JSON

    @classmethod
    def from_row(cls, row: Any) -> "ScheduledTask":
        return cls(
            id=_get(row, "id", 0),
            task_name=_get(row, "task_name", ""),
            started_at=_parse_dt(_get(row, "started_at")),
            finished_at=_parse_dt(_get(row, "finished_at")),
            success=_get(row, "success"),
            duration_seconds=_get(row, "duration_seconds"),
            details=_get(row, "details"),
        )


@dataclass
class CustomCommand:
    id: int = 0
    name: str = ""
    response: str = ""
    category: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    use_count: int = 0

    @classmethod
    def from_row(cls, row: Any) -> "CustomCommand":
        return cls(
            id=_get(row, "id", 0),
            name=_get(row, "name", ""),
            response=_get(row, "response", ""),
            category=_get(row, "category"),
            created_by=_get(row, "created_by"),
            created_at=_parse_dt(_get(row, "created_at")),
            updated_at=_parse_dt(_get(row, "updated_at")),
            use_count=_get(row, "use_count", 0),
        )


@dataclass
class NotifySubscription:
    id: int = 0
    user_id: str = ""
    server_type: str = ""
    event_type: str = ""
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Any) -> "NotifySubscription":
        return cls(
            id=_get(row, "id", 0),
            user_id=_get(row, "user_id", ""),
            server_type=_get(row, "server_type", ""),
            event_type=_get(row, "event_type", ""),
            created_at=_parse_dt(_get(row, "created_at")),
        )


@dataclass
class ConfigChange:
    id: int = 0
    timestamp: Optional[datetime] = None
    changed_by: str = ""
    config_key: str = ""
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    reason: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "ConfigChange":
        return cls(
            id=_get(row, "id", 0),
            timestamp=_parse_dt(_get(row, "timestamp")),
            changed_by=_get(row, "changed_by", ""),
            config_key=_get(row, "config_key", ""),
            old_value=_get(row, "old_value"),
            new_value=_get(row, "new_value"),
            reason=_get(row, "reason"),
        )


@dataclass
class AlertSent:
    id: int = 0
    alert_type: str = ""
    target: Optional[str] = None
    first_sent: Optional[datetime] = None
    last_sent: Optional[datetime] = None
    send_count: int = 1
    resolved: bool = False
    resolved_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Any) -> "AlertSent":
        return cls(
            id=_get(row, "id", 0),
            alert_type=_get(row, "alert_type", ""),
            target=_get(row, "target"),
            first_sent=_parse_dt(_get(row, "first_sent")),
            last_sent=_parse_dt(_get(row, "last_sent")),
            send_count=_get(row, "send_count", 1),
            resolved=bool(_get(row, "resolved", False)),
            resolved_at=_parse_dt(_get(row, "resolved_at")),
        )
