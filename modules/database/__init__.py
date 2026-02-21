"""
F28: SQLite Database Package

Zentrales Datenbank-Modul fuer alle 3 Bots und das Web-Dashboard.
Ersetzt die bisherigen JSON-Dateien durch eine SQLite-Datenbank
mit WAL-Modus fuer gleichzeitigen Zugriff.
"""

from modules.database.db_manager import get_db, init_db, close_db, get_db_path
from modules.database.models import (
    Player, PlayerSession, PlayerIP, StatsEntry, Event,
    Warn, Ticket, TicketMessage, LevelingUser, Giveaway,
    Ban, BackupRecord, AuditEntry, CommandEntry,
    WhitelistEntry, BlacklistEntry, ScheduledTask,
    CustomCommand, NotifySubscription, ConfigChange, AlertSent,
)

__all__ = [
    "get_db", "init_db", "close_db", "get_db_path",
    "Player", "PlayerSession", "PlayerIP", "StatsEntry", "Event",
    "Warn", "Ticket", "TicketMessage", "LevelingUser", "Giveaway",
    "Ban", "BackupRecord", "AuditEntry", "CommandEntry",
    "WhitelistEntry", "BlacklistEntry", "ScheduledTask",
    "CustomCommand", "NotifySubscription", "ConfigChange", "AlertSent",
]
