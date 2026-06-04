"""
F28: Schema-Migrationen — Erstellt und aktualisiert das Datenbankschema.

Verwendet PRAGMA user_version fuer Versionierung.
Jede Migration ist idempotent (CREATE TABLE IF NOT EXISTS).
"""

import aiosqlite
from utils.logger import get_logger

logger = get_logger("database.migrations")

# Aktuelle Schema-Version
CURRENT_VERSION = 8


# Komplettes Schema (23 Tabellen + Indices + FTS5)
SCHEMA_V1 = """
-- =====================================================
-- Spieler-Stammdaten
-- =====================================================
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    first_seen TIMESTAMP NOT NULL,
    last_seen TIMESTAMP NOT NULL,
    total_playtime_seconds INTEGER DEFAULT 0,
    server_type TEXT,
    discord_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
-- Spieler-Sessions (alle Join/Leave Events)
-- =====================================================
CREATE TABLE IF NOT EXISTS player_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER REFERENCES players(id),
    server_type TEXT NOT NULL,
    join_time TIMESTAMP NOT NULL,
    leave_time TIMESTAMP,
    duration_seconds INTEGER,
    ip_address TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_player ON player_sessions(player_id);
CREATE INDEX IF NOT EXISTS idx_sessions_time ON player_sessions(join_time);
CREATE INDEX IF NOT EXISTS idx_sessions_server ON player_sessions(server_type, join_time);

-- =====================================================
-- IP-Tracking pro Spieler
-- =====================================================
CREATE TABLE IF NOT EXISTS player_ips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER REFERENCES players(id),
    ip_address TEXT NOT NULL,
    first_seen TIMESTAMP NOT NULL,
    last_seen TIMESTAMP NOT NULL,
    server_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_ips_player ON player_ips(player_id);
CREATE INDEX IF NOT EXISTS idx_ips_address ON player_ips(ip_address);

-- =====================================================
-- System- und Server-Metriken (ersetzt stats_history.json)
-- =====================================================
CREATE TABLE IF NOT EXISTS stats_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    cpu_percent REAL,
    ram_percent REAL,
    ram_used_gb REAL,
    disk_percent REAL,
    disk_used_gb REAL,
    server_data TEXT
);
CREATE INDEX IF NOT EXISTS idx_stats_time ON stats_history(timestamp);

-- =====================================================
-- Event-Log
-- =====================================================
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    event_type TEXT NOT NULL,
    category TEXT,
    server_id TEXT,
    message TEXT NOT NULL,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);

-- =====================================================
-- Warn-System
-- =====================================================
CREATE TABLE IF NOT EXISTS warns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    moderator_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    points INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    active BOOLEAN DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_warns_user ON warns(user_id, active);

-- =====================================================
-- Tickets
-- =====================================================
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT,
    user_id TEXT NOT NULL,
    subject TEXT,
    status TEXT DEFAULT 'open',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    closed_by TEXT
);
CREATE TABLE IF NOT EXISTS ticket_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER REFERENCES tickets(id),
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
-- Leveling
-- =====================================================
CREATE TABLE IF NOT EXISTS leveling (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT UNIQUE NOT NULL,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 0,
    messages INTEGER DEFAULT 0,
    voice_minutes INTEGER DEFAULT 0,
    last_xp_time TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_leveling_xp ON leveling(xp DESC);

-- =====================================================
-- Giveaways
-- =====================================================
CREATE TABLE IF NOT EXISTS giveaways (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT UNIQUE,
    channel_id TEXT,
    guild_id TEXT,
    prize TEXT NOT NULL,
    winner_count INTEGER DEFAULT 1,
    ends_at TIMESTAMP,
    ended BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS giveaway_participants (
    giveaway_id INTEGER REFERENCES giveaways(id),
    user_id TEXT NOT NULL,
    PRIMARY KEY (giveaway_id, user_id)
);

-- =====================================================
-- Bans (persistent — ueberlebt Reboots!)
-- =====================================================
CREATE TABLE IF NOT EXISTS bans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL,
    player_name TEXT,
    server_type TEXT,
    reason TEXT,
    banned_by TEXT,
    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    active BOOLEAN DEFAULT TRUE,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_bans_ip ON bans(ip_address, active);
CREATE INDEX IF NOT EXISTS idx_bans_active ON bans(active, expires_at);

-- =====================================================
-- Backup-Historie
-- =====================================================
CREATE TABLE IF NOT EXISTS backup_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_type TEXT NOT NULL,
    backup_type TEXT,
    filename TEXT NOT NULL,
    size_bytes INTEGER,
    checksum TEXT,
    integrity_ok BOOLEAN,
    cloud_synced BOOLEAN DEFAULT FALSE,
    cloud_synced_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
-- Audit-Log
-- =====================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id TEXT,
    user_name TEXT,
    action TEXT NOT NULL,
    target TEXT,
    details TEXT,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

-- =====================================================
-- Command-Log
-- =====================================================
CREATE TABLE IF NOT EXISTS command_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    command_name TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT,
    guild_id TEXT,
    channel_id TEXT,
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_cmdlog_cmd ON command_log(command_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_cmdlog_user ON command_log(user_id);

-- =====================================================
-- Whitelist + Blacklist
-- =====================================================
CREATE TABLE IF NOT EXISTS whitelist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT NOT NULL,
    server_type TEXT NOT NULL,
    added_by TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_name, server_type)
);
CREATE TABLE IF NOT EXISTS blacklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT,
    ip_address TEXT,
    server_type TEXT NOT NULL,
    reason TEXT,
    added_by TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
-- Scheduled Tasks Historie
-- =====================================================
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    success BOOLEAN,
    duration_seconds REAL,
    details TEXT
);

-- =====================================================
-- Custom Commands (fuer F30)
-- =====================================================
CREATE TABLE IF NOT EXISTS custom_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    response TEXT NOT NULL,
    category TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP,
    use_count INTEGER DEFAULT 0
);

-- =====================================================
-- Spieler-Benachrichtigungen (fuer F40)
-- =====================================================
CREATE TABLE IF NOT EXISTS notify_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    server_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, server_type, event_type)
);

-- =====================================================
-- Config-Versioning
-- =====================================================
CREATE TABLE IF NOT EXISTS config_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    changed_by TEXT NOT NULL,
    config_key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_config_hist_time ON config_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_config_hist_key ON config_history(config_key);

-- =====================================================
-- Alert-Deduplizierung
-- =====================================================
CREATE TABLE IF NOT EXISTS alerts_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type TEXT NOT NULL,
    target TEXT,
    first_sent TIMESTAMP NOT NULL,
    last_sent TIMESTAMP NOT NULL,
    send_count INTEGER DEFAULT 1,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    UNIQUE(alert_type, target)
);
CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts_sent(alert_type, resolved);
"""

# FTS5 Volltext-Index (separate Anweisung wegen VIRTUAL TABLE)
FTS5_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    source,
    source_id,
    content,
    timestamp
);
"""


async def get_schema_version(db: aiosqlite.Connection) -> int:
    """Liest die aktuelle Schema-Version aus PRAGMA user_version."""
    cursor = await db.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def set_schema_version(db: aiosqlite.Connection, version: int) -> None:
    """Setzt die Schema-Version via PRAGMA user_version."""
    await db.execute(f"PRAGMA user_version = {version}")
    await db.commit()


async def run_migrations(db: aiosqlite.Connection) -> None:
    """
    Fuehrt alle ausstehenden Schema-Migrationen aus.
    Idempotent — kann bei jedem Start aufgerufen werden.
    """
    current = await get_schema_version(db)
    logger.info(f"Schema-Version: {current}, Ziel-Version: {CURRENT_VERSION}")

    if current >= CURRENT_VERSION:
        logger.info("Schema ist aktuell — keine Migration noetig")
        return

    # Version 0 → 1: Initiales Schema
    if current < 1:
        logger.info("Migration v0 → v1: Erstelle komplettes Schema (23 Tabellen)")
        await _apply_schema_v1(db)
        await set_schema_version(db, 1)
        logger.info("Migration v0 → v1 abgeschlossen")

    # Version 1 → 2: Server-Stats-Tracker Tabelle
    if current < 2:
        logger.info("Migration v1 → v2: Erstelle server_stats_tracker Tabelle")
        await _apply_migration_v2(db)
        await set_schema_version(db, 2)
        logger.info("Migration v1 → v2 abgeschlossen")

    # Version 2 → 3: Reaction-Roles Tabelle
    if current < 3:
        logger.info("Migration v2 → v3: Erstelle reaction_roles Tabelle")
        await _apply_migration_v3(db)
        await set_schema_version(db, 3)
        logger.info("Migration v2 → v3 abgeschlossen")

    # Version 3 → 4: Auto-Update-System (Modpack + SAT)
    if current < 4:
        logger.info("Migration v3 → v4: Erstelle Auto-Update-Tabellen")
        await _apply_migration_v4(db)
        await set_schema_version(db, 4)
        logger.info("Migration v3 → v4 abgeschlossen")

    # Version 4 → 5: Multi-Tenant-Fundament (guilds + guild_config + guild_modules)
    if current < 5:
        logger.info("Migration v4 → v5: Erstelle Multi-Tenant-Tabellen")
        await _apply_migration_v5(db)
        await set_schema_version(db, 5)
        logger.info("Migration v4 → v5 abgeschlossen")

    # Version 5 → 6: Leveling guild-scoped + voice_sessions (Anti-Cheat)
    if current < 6:
        logger.info("Migration v5 → v6: Leveling guild-scoped + voice_sessions")
        await _apply_migration_v6(db)
        await set_schema_version(db, 6)
        logger.info("Migration v5 → v6 abgeschlossen")

    # Version 6 → 7: Verlinkte Accounts (Linked-Accounts/Profil)
    if current < 7:
        logger.info("Migration v6 → v7: user_linked_accounts")
        await _apply_migration_v7(db)
        await set_schema_version(db, 7)
        logger.info("Migration v6 → v7 abgeschlossen")

    # Version 7 → 8: Member-Cache (Name+Avatar fuers Web-Leaderboard, E3)
    if current < 8:
        logger.info("Migration v7 → v8: member_cache")
        await _apply_migration_v8(db)
        await set_schema_version(db, 8)
        logger.info("Migration v7 → v8 abgeschlossen")

    final = await get_schema_version(db)
    logger.info(f"Alle Migrationen abgeschlossen. Schema-Version: {final}")


async def _apply_schema_v1(db: aiosqlite.Connection) -> None:
    """Erstellt das vollstaendige v1-Schema."""
    # Hauptschema ausfuehren (aufgeteilt nach Statements)
    await db.executescript(SCHEMA_V1)

    # FTS5 separat (VIRTUAL TABLE braucht eigene Ausfuehrung)
    try:
        await db.executescript(FTS5_SCHEMA)
        logger.info("FTS5 Volltext-Index erstellt")
    except Exception as e:
        # FTS5 ist optional — manche SQLite-Builds haben es nicht
        logger.warning(f"FTS5 nicht verfuegbar (optional): {e}")

    await db.commit()

    # v2+v3+v4+v5+v6-Tabellen auch gleich anlegen bei Fresh-Install
    await _apply_migration_v2(db)
    await _apply_migration_v3(db)
    await _apply_migration_v4(db)
    await _apply_migration_v5(db)
    await _apply_migration_v6(db)
    await _apply_migration_v7(db)
    await _apply_migration_v8(db)

    # Tabellen zaehlen zur Verifikation
    cursor = await db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'"
    )
    row = await cursor.fetchone()
    table_count = row[0] if row else 0
    logger.info(f"Schema erstellt: {table_count} Tabellen")


# =====================================================
# Migration v2: Server-Stats-Tracker
# =====================================================

SCHEMA_V2 = """
-- =====================================================
-- Server-Stats-Tracker (ersetzt stats_history_*.json)
-- Einzelne Metriken: uptime, player_count, savegame_size, crash
-- =====================================================
CREATE TABLE IF NOT EXISTS server_stats_tracker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_type TEXT NOT NULL,
    server_id TEXT,
    metric_type TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    value_int INTEGER,
    value_real REAL
);
CREATE INDEX IF NOT EXISTS idx_sst_lookup
    ON server_stats_tracker(server_type, metric_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_sst_server
    ON server_stats_tracker(server_type, server_id, metric_type, timestamp);
"""


async def _apply_migration_v2(db: aiosqlite.Connection) -> None:
    """Erstellt die server_stats_tracker Tabelle."""
    await db.executescript(SCHEMA_V2)
    await db.commit()
    logger.info("server_stats_tracker Tabelle erstellt")


# =====================================================
# Migration v3: Reaction-Roles
# =====================================================

SCHEMA_V3 = """
-- =====================================================
-- Reaction-Roles (ersetzt reaction_roles.json)
-- Eine Nachricht kann mehrere Emoji-Rollen-Paare haben
-- =====================================================
CREATE TABLE IF NOT EXISTS reaction_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    emoji TEXT NOT NULL,
    role_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(message_id, emoji)
);
CREATE INDEX IF NOT EXISTS idx_rr_message
    ON reaction_roles(message_id);
"""


async def _apply_migration_v3(db: aiosqlite.Connection) -> None:
    """Erstellt die reaction_roles Tabelle."""
    await db.executescript(SCHEMA_V3)
    await db.commit()
    logger.info("reaction_roles Tabelle erstellt")


# =====================================================
# Migration v4: Auto-Update-System (Modpack + SAT)
# =====================================================

SCHEMA_V4 = """
-- =====================================================
-- Modpack-Update-Log (MC + SAT)
-- Protokolliert alle Update-Vorgänge mit Crash-Recovery
-- =====================================================
CREATE TABLE IF NOT EXISTS modpack_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id TEXT NOT NULL,
    old_version TEXT NOT NULL,
    new_version TEXT NOT NULL,
    old_curseforge_id INTEGER,
    new_curseforge_id INTEGER,
    update_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress',
    update_phase TEXT,
    neoforge_updated BOOLEAN DEFAULT 0,
    old_neoforge TEXT,
    new_neoforge TEXT,
    attempts INTEGER DEFAULT 0,
    error_message TEXT,
    backup_path TEXT,
    rollback_path TEXT,
    download_url TEXT,
    download_hash_sha1 TEXT,
    file_size_bytes INTEGER,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    duration_seconds INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mu_server_status
    ON modpack_updates(server_id, status);
CREATE INDEX IF NOT EXISTS idx_mu_started
    ON modpack_updates(started_at DESC);

-- =====================================================
-- Server-Versions-Tracking (ersetzt .env-basiertes Tracking)
-- Speichert die aktuelle Version jedes Servers
-- =====================================================
CREATE TABLE IF NOT EXISTS server_versions (
    server_id TEXT PRIMARY KEY,
    display_version TEXT NOT NULL,
    curseforge_file_id INTEGER,
    curseforge_server_file_id INTEGER,
    steam_buildid TEXT,
    neoforge_version TEXT,
    updated_at TIMESTAMP NOT NULL,
    updated_by TEXT DEFAULT 'system'
);
"""


async def _apply_migration_v4(db: aiosqlite.Connection) -> None:
    """Erstellt die Auto-Update-Tabellen (modpack_updates + server_versions)."""
    await db.executescript(SCHEMA_V4)
    await db.commit()
    logger.info("Auto-Update-Tabellen erstellt (modpack_updates, server_versions)")


# =====================================================
# Migration v5: Multi-Tenant-Fundament (Community-Rebuild)
# =====================================================
# Trennt Daten/Config pro Discord-Guild. guild_id als TEXT (Snowflake,
# konsistent mit giveaways/command_log/reaction_roles). Fundament fuer
# per-Guild-Config + Feature-Flags (Temp-Voice-Hubs, Level-Config, ...).
# Bestehende Community-Tabellen (z.B. leveling) werden NICHT hier guild-scoped
# — das uebernimmt der jeweilige Feature-Rebuild (Phase C besitzt die
# leveling-Migration), um Doppel-Migrationen zu vermeiden.
# Game-Server-Tabellen (players/sessions/bans/stats) bleiben single-tenant
# (Marcos privates Monitoring, kein Multi-Guild-Bezug).

SCHEMA_V5 = """
-- =====================================================
-- Guild-Registry: jede Guild die der Bot bedient
-- =====================================================
CREATE TABLE IF NOT EXISTS guilds (
    guild_id TEXT PRIMARY KEY,
    name TEXT,
    owner_id TEXT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    active BOOLEAN DEFAULT 1
);

-- =====================================================
-- Generische Per-Guild-Settings (schema-getrieben, Dashboard-editierbar)
-- value = TEXT (JSON-encoded fuer non-str Werte)
-- =====================================================
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT DEFAULT 'system',
    PRIMARY KEY (guild_id, key)
);
CREATE INDEX IF NOT EXISTS idx_guild_config_guild ON guild_config(guild_id);

-- =====================================================
-- Feature-Flags / Modul-Toggles pro Guild
-- (Freund ohne Game-Server schaltet 'gameserver'-Modul aus, etc.)
-- =====================================================
CREATE TABLE IF NOT EXISTS guild_modules (
    guild_id TEXT NOT NULL,
    module TEXT NOT NULL,
    enabled BOOLEAN DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, module)
);
CREATE INDEX IF NOT EXISTS idx_guild_modules_guild ON guild_modules(guild_id);
"""


async def _apply_migration_v5(db: aiosqlite.Connection) -> None:
    """Erstellt die Multi-Tenant-Fundament-Tabellen (guilds, guild_config, guild_modules)."""
    await db.executescript(SCHEMA_V5)
    await db.commit()
    logger.info("Multi-Tenant-Tabellen erstellt (guilds, guild_config, guild_modules)")


# =====================================================
# Migration v6: Leveling guild-scoped + voice_sessions (Phase C Level-Rebuild)
# =====================================================
# Macht das Level-System multi-tenant-fähig (Cross-Guild-XP-Collision behoben:
# UNIQUE(user_id) -> UNIQUE(guild_id, user_id)) + legt voice_sessions an für
# echtes Voice-Anti-Cheat (statt grober 5-Min-Ticks). Leveling-CONFIG wandert
# NICHT in eine eigene Tabelle, sondern nutzt das generische guild_config (v5).
#
# Rollback-Safety (Plan P18): die leveling-Tabelle wird per recreate-copy-drop
# migriert (SQLite kann UNIQUE nicht per ALTER ändern). Bestehende XP-Daten
# bleiben erhalten (alle Spalten kopiert), Backfill guild_id = ENV GUILD_ID
# (Marcos Guild). Idempotent: läuft nur wenn leveling noch kein guild_id hat.

SCHEMA_V6_VOICE = """
-- =====================================================
-- Voice-Sessions (echtes Anti-Cheat: join/leave statt 5-Min-Tick)
-- valid=0 wenn self-deaf / allein / AFK -> kein XP
-- =====================================================
CREATE TABLE IF NOT EXISTS voice_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    channel_id TEXT,
    join_time TIMESTAMP NOT NULL,
    leave_time TIMESTAMP,
    duration_seconds INTEGER,
    xp_awarded INTEGER DEFAULT 0,
    valid INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_vs_guild_user ON voice_sessions(guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_vs_open ON voice_sessions(guild_id, user_id, leave_time);
"""


async def _apply_migration_v6(db: aiosqlite.Connection) -> None:
    """Leveling guild-scoped + voice_sessions. Idempotent + datenerhaltend."""
    # 1. voice_sessions (idempotent)
    await db.executescript(SCHEMA_V6_VOICE)

    # 2. leveling guild-scopen — nur wenn noch nicht migriert
    cursor = await db.execute("PRAGMA table_info(leveling)")
    columns = [row[1] for row in await cursor.fetchall()]
    if "guild_id" not in columns:
        # Backfill-Guild aus ENV (bestehende Daten = Marcos Haupt-Guild)
        from utils.config import get_env
        primary_guild = str(get_env("GUILD_ID", "0") or "0")

        # recreate-copy-drop (SQLite kann UNIQUE-Constraint nicht per ALTER ändern)
        await db.execute("ALTER TABLE leveling RENAME TO leveling_old")
        await db.execute(
            """
            CREATE TABLE leveling (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                messages INTEGER DEFAULT 0,
                voice_minutes INTEGER DEFAULT 0,
                last_xp_time TIMESTAMP,
                UNIQUE(guild_id, user_id)
            )
            """
        )
        # Bestehende XP-Daten erhalten, guild_id backfillen (parametrisiert)
        await db.execute(
            """
            INSERT INTO leveling
                (guild_id, user_id, xp, level, messages, voice_minutes, last_xp_time)
            SELECT ?, user_id, xp, level, messages, voice_minutes, last_xp_time
            FROM leveling_old
            """,
            (primary_guild,),
        )
        await db.execute("DROP TABLE leveling_old")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_leveling_guild_xp "
            "ON leveling(guild_id, xp DESC)"
        )
        logger.info(
            f"leveling guild-scoped migriert (Backfill guild_id={primary_guild})"
        )
    else:
        logger.info("leveling bereits guild-scoped — übersprungen")

    await db.commit()
    logger.info("Migration v6: voice_sessions + leveling guild-scoped fertig")


SCHEMA_V7_LINKED = """
-- =====================================================
-- Verlinkte Accounts (generisch: Activision/Steam/Epic/...)
-- Per-Guild isoliert (kein Cross-Guild-Leak von PII).
-- =====================================================
CREATE TABLE IF NOT EXISTS user_linked_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    account_id TEXT NOT NULL,
    linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, user_id, platform)
);
CREATE INDEX IF NOT EXISTS idx_ula_guild_user
    ON user_linked_accounts(guild_id, user_id);
"""


async def _apply_migration_v7(db: aiosqlite.Connection) -> None:
    """Verlinkte Accounts (Linked-Accounts/Profil). Idempotent."""
    await db.executescript(SCHEMA_V7_LINKED)
    await db.commit()
    logger.info("Migration v7: user_linked_accounts fertig")


SCHEMA_V8_MEMBER_CACHE = """
-- =====================================================
-- Member-Cache (E3): Anzeigename + Avatar-URL pro Guild-Member.
-- Der Web-Prozess kann Discord-Namen/Avatare nicht selbst aufloesen;
-- der Bot pflegt diesen Cache bei XP-Aktivitaet, das Dashboard joint ihn.
-- Per-Guild isoliert (kein Cross-Guild-Leak). Additiv, keine Daten-Transform.
-- =====================================================
CREATE TABLE IF NOT EXISTS member_cache (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    display_name TEXT,
    avatar_url TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_member_cache_guild
    ON member_cache(guild_id);
"""


async def _apply_migration_v8(db: aiosqlite.Connection) -> None:
    """Member-Cache (Name+Avatar fuers Web-Leaderboard, E3). Idempotent."""
    await db.executescript(SCHEMA_V8_MEMBER_CACHE)
    await db.commit()
    logger.info("Migration v8: member_cache fertig")
