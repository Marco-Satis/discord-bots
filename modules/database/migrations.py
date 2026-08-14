"""
F28: Schema-Migrationen — Erstellt und aktualisiert das Datenbankschema.

Verwendet PRAGMA user_version fuer Versionierung.
Jede Migration ist idempotent (CREATE TABLE IF NOT EXISTS).
"""

import aiosqlite
from utils.logger import get_logger

logger = get_logger("database.migrations")

# Aktuelle Schema-Version
CURRENT_VERSION = 12


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
    await db.execute(f"PRAGMA user_version = {int(version)}")  # M09-Fix: int-Guard (PRAGMA nimmt keine ?-Params)
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
        # M07-Fix: _apply_schema_v1 chained bei Fresh-Install v2..v9 mit (erreicht
        # also Schema-Version CURRENT_VERSION=9). Ohne diese lokale Korrektur bleibt
        # current=0 und die folgenden `if current < N`-Guards lassen v2..v9 ein
        # ZWEITES Mal laufen. user_version wird daher direkt auf die erreichte
        # Version gesetzt (nicht auf 1 — sonst Doppel-/Falsch-Write).
        current = CURRENT_VERSION
        await set_schema_version(db, current)
        logger.info(f"Migration v0 → v{current} (Fresh-Install) abgeschlossen")

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

    # Version 8 → 9: RBAC (Rollen→Permission-Mapping + Dashboard-Audit-Log)
    if current < 9:
        logger.info("Migration v8 → v9: rbac_role_map + dashboard_audit")
        await _apply_migration_v9(db)
        await set_schema_version(db, 9)
        logger.info("Migration v8 → v9 abgeschlossen")

    # Version 9 → 10: To-Do-Board (/todo)
    if current < 10:
        logger.info("Migration v9 → v10: todos + todo_board")
        await _apply_migration_v10(db)
        await set_schema_version(db, 10)
        logger.info("Migration v9 → v10 abgeschlossen")

    # Version 10 → 11: ungenutzten Index idx_sst_server entfernen
    if current < 11:
        logger.info("Migration v10 → v11: idx_sst_server entfernen")
        await _apply_migration_v11(db)
        await set_schema_version(db, 11)
        logger.info("Migration v10 → v11 abgeschlossen")

    # Version 11 → 12: Satisfactory-Messwerte bekommen eine Server-ID
    if current < 12:
        logger.info("Migration v11 → v12: server_id fuer Satisfactory-Messwerte")
        await _apply_migration_v12(db)
        await set_schema_version(db, 12)
        logger.info("Migration v11 → v12 abgeschlossen")

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
    await _apply_migration_v9(db)
    await _apply_migration_v10(db)
    await _apply_migration_v11(db)
    await _apply_migration_v12(db)

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
-- Ein Index, nicht zwei. Der frueher zusaetzlich angelegte idx_sst_server
-- (server_type, server_id, metric_type, timestamp) wurde von keiner Abfrage
-- benutzt: alle vier Leser und der Aufraeum-Lauf filtern server_id ueber
-- `(server_id = ? OR (server_id IS NULL AND ? IS NULL))`, und ein OR ueber
-- dieselbe Spalte kann SQLite nicht in einen Index-Zugriff aufloesen. Gemessen
-- an der Live-Datenbank (EXPLAIN QUERY PLAN): jede Abfrage nimmt idx_sst_lookup.
-- Der zweite Index kostete 16,5 MB und Schreibarbeit bei jedem Insert.
-- Entfernt in Migration v11.
CREATE INDEX IF NOT EXISTS idx_sst_lookup
    ON server_stats_tracker(server_type, metric_type, timestamp);
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
        # M38-Fix: stiller "0"-Fallback explizit warnen — sonst landen alle alten
        # XP-Daten unbemerkt in einer Default-Guild (guild_id="0") und sind im
        # guild-scoped Leaderboard nicht mehr der echten Guild zugeordnet.
        if primary_guild == "0":
            logger.warning(
                "v6-Backfill: GUILD_ID-ENV fehlt/leer — bestehende XP-Daten werden "
                "der Default-Guild (guild_id='0') zugeordnet. Setze GUILD_ID vor der "
                "Migration, damit alte XP-Daten der korrekten Guild zugeordnet werden."
            )

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


# =====================================================
# Migration v9: RBAC (Rollen→Permission-Mapping + Dashboard-Audit-Log)
# =====================================================
# Fundament fuer das Dashboard-RBAC (Spec docs/RBAC_SPEC_2026-06-04.md):
#   - rbac_role_map: bildet eine Discord-Rollen-ID auf (resource, action) ab.
#     Marco pflegt das ueber die /rbac-Seite; der eingeloggte User bekommt die
#     Rechte aller seiner Discord-Rollen (Vereinigung) + Member-Default (view).
#   - dashboard_audit: protokolliert wer was im Dashboard geaendert hat (B4).
# Beide additiv + idempotent. Owner bleibt ENV-basiert (OWNER_ID), NICHT ueber
# die Map regelbar (Selbst-Aussperr-Schutz).

SCHEMA_V9_RBAC = """
-- =====================================================
-- RBAC: Discord-Rolle -> Dashboard-Bereich + Aktion
-- role_id = Discord-Rollen-Snowflake (TEXT, konsistent mit dem Schema)
-- resource = minecraft | satisfactory | leveling | ... (siehe modules/rbac.py)
-- action   = view | edit | control
-- PRIMARY KEY deckt Lookups "WHERE role_id IN (...)" ab (role_id ist links).
-- =====================================================
CREATE TABLE IF NOT EXISTS rbac_role_map (
    role_id   TEXT NOT NULL,
    resource  TEXT NOT NULL,
    action    TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT DEFAULT 'system',
    PRIMARY KEY (role_id, resource, action)
);
CREATE INDEX IF NOT EXISTS idx_rbac_resource
    ON rbac_role_map(resource, action);

-- =====================================================
-- Dashboard-Audit-Log: wer hat was im Dashboard geaendert (B4)
-- detail = JSON-Text (was geaendert; KEINE Secrets), ip = Client-IP
-- =====================================================
CREATE TABLE IF NOT EXISTS dashboard_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    discord_id TEXT,
    username   TEXT,
    resource   TEXT,
    action     TEXT,
    detail     TEXT,
    ip         TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON dashboard_audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_discord ON dashboard_audit(discord_id);
"""


async def _apply_migration_v9(db: aiosqlite.Connection) -> None:
    """RBAC-Mapping + Dashboard-Audit-Log. Additiv, idempotent."""
    await db.executescript(SCHEMA_V9_RBAC)
    await db.commit()
    logger.info("Migration v9: rbac_role_map + dashboard_audit fertig")


SCHEMA_V10_TODOS = """
-- =====================================================
-- To-Do-Board (/todo) — gemeinsame Bau-Ziele pro Board
-- board      = 'satisfactory' (weitere Boards ohne Schema-Aenderung moeglich)
-- created_by = Discord-User-ID als TEXT (Schema-Konvention, s. dashboard_audit)
-- done       = 0/1; abhaken darf jeder Spieler, bearbeiten/loeschen nur der
--              Ersteller (Owner/Admin ausgenommen)
-- =====================================================
CREATE TABLE IF NOT EXISTS todos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    board           TEXT NOT NULL DEFAULT 'satisfactory',
    text            TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    created_by_name TEXT,
    created_at      TIMESTAMP NOT NULL,
    edited_at       TIMESTAMP,
    done            INTEGER NOT NULL DEFAULT 0,
    done_by         TEXT,
    done_by_name    TEXT,
    done_at         TIMESTAMP
);
-- Deckt den Haupt-Lookup ab: offene/erledigte Eintraege eines Boards in Reihenfolge
CREATE INDEX IF NOT EXISTS idx_todos_board_done
    ON todos(board, done, id);

-- =====================================================
-- Sticky-Board: wo haengt das Board, welche Nachricht ist die aktuelle
-- Genau eine Zeile pro Board (PK board)
-- =====================================================
CREATE TABLE IF NOT EXISTS todo_board (
    board      TEXT PRIMARY KEY,
    guild_id   TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    message_id TEXT,
    updated_at TIMESTAMP
);
"""


async def _apply_migration_v10(db: aiosqlite.Connection) -> None:
    """To-Do-Board fuer /todo. Additiv, idempotent."""
    await db.executescript(SCHEMA_V10_TODOS)
    await db.commit()
    logger.info("Migration v10: todos + todo_board fertig")


# =====================================================
# Migration v11: ungenutzten Index entfernen
# =====================================================


async def _apply_migration_v11(db: aiosqlite.Connection) -> None:
    """
    Entfernt idx_sst_server auf server_stats_tracker.

    Die Tabelle trug zwei Indizes. Benutzt wird nur idx_sst_lookup — belegt
    per EXPLAIN QUERY PLAN gegen die Live-Datenbank fuer alle vier Leser in
    modules/monitoring/stats_tracker.py und den Retention-DELETE. Der Grund
    liegt an der Abfrageform: server_id wird als
    `(server_id = ? OR (server_id IS NULL AND ? IS NULL))` geprueft, und diese
    OR-Verzweigung ueber dieselbe Spalte laesst sich nicht in einen
    Index-Zugriff uebersetzen. Damit war die Spalte an Position 2 von
    idx_sst_server wertlos, der Index insgesamt toter Ballast: 16,5 MB Platz
    plus Pflegeaufwand bei jedem der rund 3.100 taeglichen Inserts.

    DROP INDEX ist umkehrbar — der Index laesst sich aus SCHEMA_V2 jederzeit
    neu anlegen, ohne dass Daten verloren gehen.
    """
    await db.execute("DROP INDEX IF EXISTS idx_sst_server")
    await db.commit()
    logger.info("Migration v11: idx_sst_server entfernt (ungenutzt)")


# =====================================================
# Migration v12: Server-ID fuer Satisfactory-Messwerte
# =====================================================


async def _apply_migration_v12(db: aiosqlite.Connection) -> None:
    """
    Traegt fuer die vorhandenen Satisfactory-Messwerte die Server-ID nach.

    Solange es genau einen Satisfactory-Server gab, blieb `server_id` leer —
    Minecraft fuellte sie von Anfang an ('bmc', 'vanilla'). Mit einem zweiten
    Satisfactory-Server braucht jede Instanz ihre ID, sonst laufen beide
    Verlaeufe in einen Topf und weder Uptime noch Spitzenbelegung stimmen
    danach fuer einen von beiden.

    Die vorhandene Historie gehoert dem heutigen Server, also bekommt sie
    dessen ID. Die ID ist bewusst fest verdrahtet und wird NICHT aus
    SAT_SERVER_IDS gelesen: eine Migration muss auf jeder Datenbank dasselbe
    tun, unabhaengig davon, wie die Umgebung gerade aussieht.

    Minecraft-Zeilen (auch die 62.593 des stillgelegten Vanilla) bleiben
    unberuehrt.
    """
    cursor = await db.execute(
        "UPDATE server_stats_tracker SET server_id = 'MAIN' "
        "WHERE server_type = 'sat' AND server_id IS NULL"
    )
    await db.commit()
    logger.info(f"Migration v12: {cursor.rowcount} Satisfactory-Messwerte auf server_id='MAIN'")
