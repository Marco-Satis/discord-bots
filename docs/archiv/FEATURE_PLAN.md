# Feature-Plan — Discord Bot System v3.x+

> **Stand:** 22. Februar 2026 | **Basis:** v4.0.0

---

## Priorisierung

| Prio | Bedeutung |
|------|-----------|
| P1 | Hohe Prioritaet — Sicherheit, Stabilitaet, Kernfunktionen |
| P2 | Sinnvolle Erweiterung — klarer Mehrwert, mittlerer Aufwand |
| P3 | Nice-to-have — Spielerei, Komfort, langfristig |

---

## Offene Features

### 27. Gameserver Health-Check mit Auto-Restart (P1)

Automatischer Health-Check fuer alle Gameserver (SAT + MC). Wenn die Server-API nicht mehr antwortet obwohl der Prozess laeuft (haengender Server), wird automatisch ein Restart ausgefuehrt.

**Problem:**
- Satisfactory-Server-Prozess laeuft laut systemd (`active (running)`), aber die Server-API antwortet nicht mehr
- Monitor Bot meldet "0/0 Spieler", obwohl der Server eigentlich online sein sollte
- Im Spiel: "Failed to connect server API"
- Aktuell: Manueller Restart per SSH noetig (`sudo systemctl restart satisfactory.service`)

**Loesung — Intelligenter Health-Check:**
- Neuer Background-Task im Monitor Bot: Prueft alle 2-3 Minuten ob die Server-API tatsaechlich antwortet
- Unterscheidung zwischen "Prozess laeuft" (systemd) und "API erreichbar" (Query/RCON)
- Wenn Prozess laeuft ABER API X-mal hintereinander nicht antwortet → Auto-Restart

**Health-Check pro Server-Typ:**

Satisfactory:
- Query an Port 15777 (ServerQueryPort) — Lightweight Status Query
- Erwartete Antwort: Server-Name, Spieleranzahl, State
- Timeout: 10 Sekunden
- Fehlschlaege bis Restart: 3 aufeinanderfolgende (= ~6-9 Minuten keine Antwort)

Minecraft (BMC + Vanilla):
- RCON-Ping oder Server List Ping (Port 25565/25566)
- Alternative: `list` per RCON senden und auf Antwort pruefen
- Timeout: 10 Sekunden
- Fehlschlaege bis Restart: 3 aufeinanderfolgende

**Auto-Restart Ablauf:**
1. Health-Check schlaegt X-mal fehl → Server als "haengend" markiert
2. Benachrichtigung im Admin-Channel: "⚠️ {Server} API antwortet nicht (Prozess laeuft). Auto-Restart wird ausgefuehrt..."
3. `systemctl restart {service}` ausfuehren
4. 2 Minuten warten, dann erneut pruefen
5. Wenn API wieder antwortet → Erfolgs-Meldung im Admin-Channel: "✅ {Server} nach Auto-Restart wieder erreichbar"
6. Wenn nach Restart immer noch nicht erreichbar → Warnung: "🔴 {Server} antwortet auch nach Restart nicht. Manueller Eingriff noetig!"
7. Cooldown: Max. 1 Auto-Restart pro 30 Minuten pro Server (verhindert Restart-Loop)

**Konfiguration (config.json):**
- `health_check_enabled`: true/false (Standard: true)
- `health_check_interval`: Sekunden zwischen Checks (Standard: 180 = 3 Min)
- `health_check_failures_before_restart`: Anzahl Fehlschlaege (Standard: 3)
- `health_check_restart_cooldown`: Sekunden zwischen Auto-Restarts (Standard: 1800 = 30 Min)
- `health_check_auto_restart`: true/false — ob Auto-Restart aktiv ist oder nur Benachrichtigung (Standard: true)
- Pro Server ueberschreibbar

**Benachrichtigungen:**
- API nicht erreichbar (nach X Fehlschlaegen): Admin-Channel
- Auto-Restart ausgefuehrt: Admin-Channel
- Server nach Restart wieder online: Admin-Channel
- Server nach Restart immer noch offline: Admin-Channel + optional E-Mail

**Abgrenzung zum bestehenden Monitoring:**
- Bestehender Monitor Bot prueft ob der Prozess laeuft (systemd status) → bleibt
- Neuer Health-Check prueft ob die API antwortet (Query/RCON) → NEU
- Beide Checks ergaenzen sich: Prozess-Check erkennt Crashes, API-Check erkennt haengende Server

**Technische Umsetzung:**
- Integration in bestehenden Monitor Bot als neuer Background-Task
- Nutzt bestehende Query-/RCON-Funktionen aus `modules/`
- Restart per `asyncio.create_subprocess_exec("sudo", "systemctl", "restart", service_name)`
- Erfordert: botuser hat `sudoers`-Eintrag fuer `systemctl restart` (bereits vorhanden)

**Aufwand:** ~3-4 Stunden
**Dateien:** Neues Modul `modules/monitoring/health_checker.py`, Erweiterung `bots/monitor_bot.py`
**Abhaengigkeiten:** Bestehende Query-/RCON-Module

### 28. SQLite Datenbank-Upgrade (P1)

Komplette Migration aller JSON-basierten Datenspeicher auf SQLite. Loest Race Conditions,
verbessert Performance und ermoeglicht erweiterte Features (F55-F60).

**WICHTIG:** Dieses Feature ist die Basis fuer viele nachfolgende Features. Alle betroffenen
Module muessen in einem Rutsch umgebaut werden, damit keine Mischung aus JSON und SQLite entsteht.

---

#### Aktueller Stand — Probleme mit JSON

**Race Conditions (aktive Bugs):**
- `stats_history.json` — StatsCollector schreibt alle 5 Min, Dashboard liest bei jedem Request → korrupte Daten moeglich
- `player_stats.json` — PlayerTracker schreibt bei jedem Join/Leave, mehrere MC-Server gleichzeitig → Datenverlust
- `warns.json`, `leveling.json`, `giveaways.json` — Haeufige Schreibzugriffe ohne Concurrency-Kontrolle
- `blacklist.json`, `whitelist.json` — Geschrieben waehrend Gameplay via RCON, gleichzeitig gelesen von Dashboard

**Performance-Probleme:**
- `stats_history.json` waechst auf 5-20 MB (8640 Eintraege) — wird bei jedem Dashboard-Aufruf komplett geladen
- `player_stats.json` waechst mit Spieleranzahl unbegrenzt — komplexe Queries (Top 10, Zeitraeume) erfordern komplettes Laden + Python-Loops
- `leveling.json` waechst pro User — Leaderboard-Berechnung laedt gesamte Datei

**Unbegrenztes Wachstum (keine automatische Bereinigung):**
- `stats_history.json` (Ringbuffer hilft, aber Datei wird trotzdem gross)
- `player_stats.json` (100 Sessions/Spieler, aber Spieleranzahl unbegrenzt)
- `warns.json` (kein automatischer Verfall)
- `tickets.json` (Transcripts koennen sehr gross werden)
- `leveling.json` (waechst pro neuem User)
- `snapshot_*.json` (woechentliche Snapshots akkumulieren)

---

#### Migrations-Plan — Was wird migriert, was bleibt JSON

**→ WIRD ZU SQLITE MIGRIERT (nach Datenbank aufgeteilt):**

**Datenbank 1: `data/botdata.db` (Haupt-Datenbank, alle 3 Bots + Dashboard)**

| Tabelle | Quelle (JSON) | Geschrieben von | Gelesen von | Schreib-Freq |
|---------|---------------|-----------------|-------------|--------------|
| `players` | `player_stats.json` | PlayerTracker (Monitor) | Dashboard, Profil-Command, Reports | Pro Join/Leave |
| `player_sessions` | `player_stats.json` (sessions Array) | PlayerTracker | Leaderboard, Profil, Analytics | Pro Join/Leave |
| `player_ips` | `player_ip_tracker_*.json` | PlayerIPTracker | Ban-System, Security-Dashboard | Pro IP-Sichtung |
| `stats_history` | `stats_history.json` | StatsCollector (Monitor) | Dashboard Charts, Forecasting, Analytics | Alle 5 Min |
| `events` | `events.json` | StatusWriter (Monitor) | Dashboard Event-Feed, Suche | Alle 30s |
| `warns` | `warns.json` | WarnManager (Admin) | Warn-Commands, Profil, Dashboard | Pro Warn |
| `tickets` | `tickets.json` | TicketManager (Admin) | Ticket-Commands, Dashboard | Pro Ticket |
| `ticket_transcripts` | `tickets.json` (transcript Feld) | TicketManager | Ticket-Archiv | Pro Ticket-Close |
| `leveling` | `leveling.json` | LevelingManager (Admin) | Leaderboard, Profil | Pro Message/Voice |
| `giveaways` | `giveaways.json` | GiveawayManager (Admin) | Giveaway-Commands | Pro Giveaway-Event |
| `blacklist` | `blacklist.json` | Ban-System (GameServer) | Kick/Ban, Dashboard | Pro Ban |
| `mc_blacklist` | `mc_blacklist.json` | MC Ban-System | MC Kick/Ban | Pro Ban |
| `whitelist` | `whitelist.json` | Whitelist-Modul | RCON-Commands | Pro Whitelist-Aenderung |
| `backup_history` | `backup_history.json` | BackupManager | Backup-Commands, Dashboard | Pro Backup |
| `audit_log` | Aktuell nur Logs | AuditLogger (Admin) | Dashboard Audit-Seite, Suche | Pro Admin-Aktion |
| `command_log` | command_logger Output | CommandLogger | Command-Statistik (F59) | Pro Command |
| `custom_commands` | `custom_commands.json` (F30 neu) | CustomCommands Cog | Command-Handler | Pro Aenderung |
| `notify_subscriptions` | `notify_subscriptions.json` (F40 neu) | NotifyCog | Benachrichtigungs-System | Pro Subscribe |
| `bans` | iptables-Regeln (fluechtig!) | Ban-System | IP-Security, Dashboard, Boot-Restore | Pro Ban |
| `scheduled_tasks` | Scheduler State | SchedulerCog | Task-Historie (F56) | Pro Task-Run |
| `config_history` | NEU (kein JSON-Vorgaenger) | Dashboard, Hot-Reload, Migration | Config-Audit, Rollback | Pro Config-Aenderung |
| `alerts_sent` | NEU (kein JSON-Vorgaenger) | Alle Alerting-Module | Alert-Deduplizierung | Pro Alert |

**→ BLEIBT JSON (zu klein, zu haeufig, oder Bridge-Dateien):**

| Datei | Grund |
|-------|-------|
| `data/monitor/*_status.json` | StatusWriter-Bridge zum Dashboard, alle 30s geschrieben, <1 KB, atomar via temp-Datei |
| `data/monitor/*_players.json` | Kleine Spielerlisten, haeufig geschrieben, nur vom Dashboard gelesen |
| `data/*/bot_status.json` | Bot-Status <1 KB, haeufig geschrieben |
| `config/config.json` | Manuell bearbeitet, selten geaendert |
| `config/.env` | Umgebungsvariablen |
| `data/admin/ticket_config.json` | Einmalig konfiguriert, <1 KB |
| `data/admin/temp_voice_config.json` | Einmalig konfiguriert, <1 KB |
| `data/admin/leveling_config.json` | Selten geaendert, <5 KB |
| `data/admin/audit_config.json` | Einmalig konfiguriert, <1 KB |
| `data/admin/ts_channels.json` | Selten geaendert, <10 KB |

---

#### Schema-Design (SQLite Tabellen)

```sql
-- Spieler-Stammdaten
CREATE TABLE players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    first_seen TIMESTAMP NOT NULL,
    last_seen TIMESTAMP NOT NULL,
    total_playtime_seconds INTEGER DEFAULT 0,
    server_type TEXT,  -- 'satisfactory', 'mc_vanilla', 'mc_bmc'
    discord_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Spieler-Sessions (alle Join/Leave Events)
CREATE TABLE player_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER REFERENCES players(id),
    server_type TEXT NOT NULL,
    join_time TIMESTAMP NOT NULL,
    leave_time TIMESTAMP,
    duration_seconds INTEGER,
    ip_address TEXT
);
CREATE INDEX idx_sessions_player ON player_sessions(player_id);
CREATE INDEX idx_sessions_time ON player_sessions(join_time);
CREATE INDEX idx_sessions_server ON player_sessions(server_type, join_time);

-- IP-Tracking pro Spieler
CREATE TABLE player_ips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER REFERENCES players(id),
    ip_address TEXT NOT NULL,
    first_seen TIMESTAMP NOT NULL,
    last_seen TIMESTAMP NOT NULL,
    server_type TEXT
);
CREATE INDEX idx_ips_player ON player_ips(player_id);
CREATE INDEX idx_ips_address ON player_ips(ip_address);

-- System- und Server-Metriken (ersetzt stats_history.json Ringbuffer)
CREATE TABLE stats_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    -- System
    cpu_percent REAL, ram_percent REAL, ram_used_gb REAL,
    disk_percent REAL, disk_used_gb REAL,
    -- Pro Server als separate Zeilen oder JSON-Blob
    server_data TEXT  -- JSON: [{id, status, players, cpu, ram}]
);
CREATE INDEX idx_stats_time ON stats_history(timestamp);

-- Event-Log
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    event_type TEXT NOT NULL,  -- 'info', 'warning', 'error', 'success'
    category TEXT,  -- 'server', 'player', 'backup', 'security', 'system'
    server_id TEXT,
    message TEXT NOT NULL,
    details TEXT  -- Optional JSON fuer extra Daten
);
CREATE INDEX idx_events_time ON events(timestamp);
CREATE INDEX idx_events_type ON events(event_type);
CREATE INDEX idx_events_category ON events(category);

-- Warn-System
CREATE TABLE warns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    moderator_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    points INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,  -- NEU: Automatischer Verfall
    active BOOLEAN DEFAULT TRUE
);
CREATE INDEX idx_warns_user ON warns(user_id, active);

-- Tickets
CREATE TABLE tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT,
    user_id TEXT NOT NULL,
    subject TEXT,
    status TEXT DEFAULT 'open',  -- 'open', 'closed', 'archived'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    closed_by TEXT
);
CREATE TABLE ticket_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER REFERENCES tickets(id),
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Leveling
CREATE TABLE leveling (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT UNIQUE NOT NULL,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 0,
    messages INTEGER DEFAULT 0,
    voice_minutes INTEGER DEFAULT 0,
    last_xp_time TIMESTAMP
);
CREATE INDEX idx_leveling_xp ON leveling(xp DESC);

-- Giveaways
CREATE TABLE giveaways (
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
CREATE TABLE giveaway_participants (
    giveaway_id INTEGER REFERENCES giveaways(id),
    user_id TEXT NOT NULL,
    PRIMARY KEY (giveaway_id, user_id)
);

-- Bans (persistent — ueberlebt Reboots!)
CREATE TABLE bans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL,
    player_name TEXT,
    server_type TEXT,  -- 'satisfactory', 'mc_vanilla', 'mc_bmc', 'all'
    reason TEXT,
    banned_by TEXT,  -- 'admin', 'timeout-system', 'fail2ban'
    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,  -- NULL = permanent
    active BOOLEAN DEFAULT TRUE,
    source TEXT  -- 'dashboard', 'bot_command', 'auto'
);
CREATE INDEX idx_bans_ip ON bans(ip_address, active);
CREATE INDEX idx_bans_active ON bans(active, expires_at);

-- Backup-Historie
CREATE TABLE backup_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_type TEXT NOT NULL,
    backup_type TEXT,  -- 'auto', 'manual', 'pre-restart', 'pre-update'
    filename TEXT NOT NULL,
    size_bytes INTEGER,
    checksum TEXT,  -- SHA256 (fuer F33 Integritaetscheck)
    integrity_ok BOOLEAN,  -- Ergebnis des Integritaetschecks
    cloud_synced BOOLEAN DEFAULT FALSE,  -- OneDrive-Sync-Status (fuer F36)
    cloud_synced_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Audit-Log (erweitert — fuer Dashboard + Discord)
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id TEXT,  -- Discord User-ID oder 'system' oder 'dashboard'
    user_name TEXT,
    action TEXT NOT NULL,  -- 'kick', 'ban', 'restart', 'config_change', etc.
    target TEXT,  -- Betroffener Spieler/Server/Config
    details TEXT,  -- JSON mit Extra-Infos
    source TEXT  -- 'bot', 'dashboard', 'auto', 'scheduler'
);
CREATE INDEX idx_audit_time ON audit_log(timestamp);
CREATE INDEX idx_audit_action ON audit_log(action);

-- Command-Nutzung (fuer F59)
CREATE TABLE command_log (
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
CREATE INDEX idx_cmdlog_cmd ON command_log(command_name, timestamp);
CREATE INDEX idx_cmdlog_user ON command_log(user_id);

-- Whitelist + Blacklist
CREATE TABLE whitelist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT NOT NULL,
    server_type TEXT NOT NULL,
    added_by TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_name, server_type)
);
CREATE TABLE blacklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT,
    ip_address TEXT,
    server_type TEXT NOT NULL,
    reason TEXT,
    added_by TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Scheduled Tasks Historie (fuer F56 Retention)
CREATE TABLE scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT NOT NULL,  -- 'backup_auto', 'restart_daily', 'update_check', etc.
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    success BOOLEAN,
    duration_seconds REAL,
    details TEXT  -- JSON
);

-- Custom Commands (fuer F30)
CREATE TABLE custom_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    response TEXT NOT NULL,  -- Kann Embed-JSON sein
    category TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP,
    use_count INTEGER DEFAULT 0
);

-- Spieler-Benachrichtigungen (fuer F40)
CREATE TABLE notify_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    server_type TEXT NOT NULL,
    event_type TEXT NOT NULL,  -- 'online', 'offline', 'update', 'restart'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, server_type, event_type)
);

-- Config-Versioning (Aenderungshistorie fuer config.json)
CREATE TABLE config_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    changed_by TEXT NOT NULL,  -- 'dashboard', 'hot-reload', 'manual', 'migration'
    config_key TEXT NOT NULL,  -- 'features.health_check_enabled', 'thresholds.ram_warning', etc.
    old_value TEXT,
    new_value TEXT,
    reason TEXT  -- Optional: Warum geaendert
);
CREATE INDEX idx_config_hist_time ON config_history(timestamp);
CREATE INDEX idx_config_hist_key ON config_history(config_key);

-- Alert-Deduplizierung (verhindert Spam bei wiederholten Warnungen)
CREATE TABLE alerts_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type TEXT NOT NULL,  -- 'disk_warning', 'port_closed', 'crash', 'ssl_expiry', etc.
    target TEXT,  -- Server-ID, Port-Nummer, etc.
    first_sent TIMESTAMP NOT NULL,
    last_sent TIMESTAMP NOT NULL,
    send_count INTEGER DEFAULT 1,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    UNIQUE(alert_type, target)
);
CREATE INDEX idx_alerts_type ON alerts_sent(alert_type, resolved);

-- FTS5 Volltextsuche (fuer F55)
CREATE VIRTUAL TABLE search_index USING fts5(
    source,      -- 'event', 'audit', 'player', 'command', 'ticket'
    source_id,
    content,
    timestamp
);
```

---

#### Betroffene Module — Was muss geaendert werden

**Neue Dateien:**
- `modules/database/__init__.py` — Package
- `modules/database/db_manager.py` — Zentrale DB-Verwaltung (Connection-Pool, WAL-Modus, Migrations)
- `modules/database/migrations.py` — Schema-Versionierung + Auto-Migration
- `modules/database/models.py` — Datenklassen fuer typisierte Abfragen
- `modules/database/json_importer.py` — Einmalige Migration bestehender JSON-Daten

**Bestehende Module die angepasst werden muessen:**

| Modul | Aenderung | Aufwand |
|-------|-----------|---------|
| `modules/monitoring/player_tracker.py` | JSON → SQLite (players, sessions Tabellen) | Mittel |
| `modules/monitoring/player_ip_tracker.py` | JSON → SQLite (player_ips Tabelle) + Ban-Persistenz | Mittel |
| `modules/monitoring/stats_collector.py` | JSON → SQLite (stats_history Tabelle) | Klein |
| `modules/monitoring/status_writer.py` | events.json → SQLite (events Tabelle), Status-JSON bleibt | Klein |
| `modules/backup/backup_manager.py` | JSON → SQLite (backup_history) + Checksum-Feld | Klein |
| `cogs/warn_cog.py` + WarnManager | JSON → SQLite (warns Tabelle) + Verfall-Logik | Klein |
| `cogs/tickets_cog.py` + TicketManager | JSON → SQLite (tickets, ticket_messages) | Mittel |
| `cogs/leveling_cog.py` + LevelingManager | JSON → SQLite (leveling Tabelle) | Klein |
| `cogs/giveaway_cog.py` + GiveawayManager | JSON → SQLite (giveaways, participants) | Klein |
| `modules/command_logger.py` | Log-Output → SQLite (command_log) | Klein |
| `modules/audit_logger.py` | Erweitern auf SQLite (audit_log) | Klein |
| `web/routes/analytics_route.py` | JSON-Parsing → SQL-Queries | Mittel |
| `web/routes/dashboard.py` | Events aus SQLite statt JSON | Klein |
| `web/routes/server_detail.py` | Player-Daten aus SQLite | Klein |
| `web/routes/admin_bot_route.py` | Warns, Tickets, Leveling aus SQLite | Mittel |
| `bots/monitor_bot.py` | DB-Init beim Start, an Player/Stats-Module weitergeben | Klein |
| `bots/gameserver_bot.py` | DB-Init, Whitelist/Blacklist aus SQLite | Klein |
| `bots/admin_bot.py` | DB-Init, an alle Admin-Cogs weitergeben | Klein |

---

#### Verbesserungen fuer bestehende Features durch SQLite

| Bestehendes Feature | Verbesserung |
|---------------------|-------------|
| **Kick/Ban (iptables)** | Bans persistent in DB → ueberlebt Server-Reboots, automatisch wiederhergestellt beim Boot |
| **Timeout-System** | Temp-Bans mit `expires_at` in DB → automatisches Entbannen auch nach Bot-Neustart |
| **Warn-System** | Automatischer Verfall (`expires_at`), kein unbegrenztes Wachstum |
| **Leveling** | Leaderboard wird ein simpler `SELECT ... ORDER BY xp DESC LIMIT 10` statt ganze Datei laden |
| **Player-Tracking** | "Wer war letzte Woche am meisten online?" = ein SQL-Query statt Python-Loop ueber alle Sessions |
| **Dashboard Analytics** | Charts laden in <100ms statt mehrere Sekunden (kein 20MB JSON parsen) |
| **Backup-System** | Checksum + Integritaets-Status direkt in DB → F33 wird trivial |
| **Event-Log** | Filtern nach Typ/Zeitraum/Server effizient moeglich |
| **IP-Tracking** | Historische IPs pro Spieler → Alt-Account-Erkennung, VPN-Wechsel sichtbar |
| **Ticket-System** | Transcripts als separate Tabelle → grosse Tickets verlangsamen nicht die Uebersicht |
| **Config-Management** | Jede Aenderung an config.json wird in `config_history` protokolliert → Rollback moeglich, nachvollziehbar wer wann was geaendert hat |
| **Alerting-System** | `alerts_sent` Tabelle verhindert Alert-Spam: gleiche Warnung wird nicht 100x gesendet, sondern nur 1x + Zaehler erhoehen. Erst nach Resolved erneut senden |
| **Ban-System (Temp-Bans)** | Background-Task prueft regelmaessig `expires_at` in `bans` Tabelle → abgelaufene Bans automatisch entfernen (iptables + DB-Flag) auch nach Bot-Neustart |

---

#### Boot-Restore fuer iptables-Bans

Kritische Verbesserung: Beim Start von Monitor/GameServer Bot:
1. Lade alle aktiven Bans aus `bans` Tabelle (`WHERE active = TRUE`)
2. Pruefe ob `expires_at` abgelaufen → deaktiviere abgelaufene Bans
3. Fuer alle noch aktiven Bans: `iptables -I INPUT -s <ip> -j REJECT` + OUTPUT
4. Log: "X Bans aus Datenbank wiederhergestellt"

#### Ban-Expiry Background-Task

Laufender Task der regelmaessig temporaere Bans prueft:
1. Alle 60 Sekunden: `SELECT * FROM bans WHERE active = TRUE AND expires_at IS NOT NULL AND expires_at < NOW()`
2. Fuer jeden abgelaufenen Ban: `iptables -D INPUT -s <ip> -j REJECT` + OUTPUT
3. `UPDATE bans SET active = FALSE WHERE id = ?`
4. Event-Log: "Ban fuer {ip} ({player}) abgelaufen und entfernt"
5. Optional: Discord-Benachrichtigung bei Entbannung

#### Config-Versioning

Bei jeder Aenderung an config.json (via Dashboard, Hot-Reload, oder Migration):
1. Vorherigen Wert lesen
2. Neuen Wert setzen
3. Differenz in `config_history` schreiben (key, old_value, new_value, changed_by)
4. Dashboard: Aenderungshistorie anzeigen mit Rollback-Button
5. Warnung bei kritischen Aenderungen (z.B. `health_check_enabled` auf false)

#### Alert-Deduplizierung

Verhindert Alert-Spam bei wiederkehrenden Problemen:
1. Vor dem Senden: Pruefe `alerts_sent` ob gleicher Typ+Target existiert UND nicht resolved
2. Falls ja: Nur `send_count` und `last_sent` aktualisieren, KEINEN neuen Alert senden
3. Falls nein: Alert senden + neuen Eintrag in `alerts_sent`
4. Wenn Problem geloest: `resolved = TRUE, resolved_at = NOW()` setzen
5. Konfigurierbar: Minimaler Abstand zwischen gleichen Alerts (Standard: 30 Minuten)

---

#### Migrations-System

Beim Bot-Start wird automatisch geprueft:
1. Existiert `data/botdata.db`? Falls nein → Schema erstellen
2. Schema-Version pruefen (`PRAGMA user_version`)
3. Falls aelter als aktuelle Version → Migrations ausfuehren (ALTER TABLE, neue Tabellen)
4. Falls JSON-Dateien existieren aber DB leer → einmalige Import-Migration

**JSON-Import (einmalig):**
- `json_importer.py` liest alle alten JSON-Dateien
- Importiert Daten in die neuen Tabellen
- Benennt alte JSON-Dateien um: `player_stats.json` → `player_stats.json.migrated`
- Rollback moeglich: Alte Dateien bleiben erhalten

---

#### Technische Umsetzung

- **Library:** `aiosqlite` fuer async SQLite-Zugriff (requirements.txt)
- **WAL-Modus:** `PRAGMA journal_mode=WAL` — erlaubt gleichzeitiges Lesen und Schreiben
- **Connection-Pool:** Ein Shared `aiosqlite.Connection` pro Bot-Prozess
- **Cross-Bot-Zugriff:** Alle 3 Bots + Dashboard nutzen dieselbe `data/botdata.db`
- **Backup:** SQLite DB wird in regulaere Backup-Rotation aufgenommen
- **Index-Strategie:** Indices auf alle haeufig abgefragten Spalten (siehe Schema)

**Aufwand:** ~10-14 Stunden (erhoehrt wegen Umfang: 20+ Module betroffen)
**Dateien:** `modules/database/` (neu, 5 Dateien), 17+ bestehende Module anpassen
**Abhaengigkeiten:** aiosqlite (requirements.txt)

### 29. Live-Updates via WebSocket/SSE (P2)

Dashboard aktualisiert sich in Echtzeit ohne Seiten-Reload.

**Aktueller Stand:**
- HTMX-Polling fuer dynamische Inhalte (manueller Refresh / Intervall-basiert)
- Kein Push-Mechanismus — Dashboard zeigt veraltete Daten bis zum naechsten Reload

**Geplante Umsetzung:**
- Server-Sent Events (SSE) fuer unidirektionale Updates (Server → Browser)
- SSE bevorzugt gegenueber WebSocket weil einfacher, kein extra Library noetig, und reicht fuer Status-Updates
- Endpoints: `/api/sse/status` (Server-Status), `/api/sse/events` (Event-Stream)
- Dashboard-Kacheln, Charts und Spielerlisten aktualisieren sich automatisch alle 5-10 Sekunden
- Reconnect-Logik im Frontend bei Verbindungsabbruch
- Fallback auf HTMX-Polling wenn SSE nicht unterstuetzt wird

**Vorteile:**
- Sofortige Sichtbarkeit von Server-Crashes, Spieler-Joins, Restarts
- Kein manuelles Neuladen noetig
- Geringerer Server-Load als Polling (eine offene Verbindung statt viele Requests)

**Aufwand:** ~4-5 Stunden
**Dateien:** `web/routes/sse_route.py` (neu), Anpassung aller Dashboard-Templates
**Abhaengigkeiten:** Keine (SSE ist nativ in FastAPI via `StreamingResponse`)

### 30. Custom-Commands System (P2)

Admins erstellen eigene Text-Antwort-Commands ohne Code-Aenderungen.

**Funktionsumfang:**
- `/customcmd add <name> <antwort>` — Neuen Command erstellen
- `/customcmd remove <name>` — Command loeschen
- `/customcmd list` — Alle Custom-Commands auflisten
- `/customcmd edit <name> <neue_antwort>` — Antwort bearbeiten
- Antwort unterstuetzt Embed-Format (Titel, Beschreibung, Farbe, Bild)
- Variablen: `{user}`, `{server}`, `{date}`, `{membercount}`
- Kategorien/Tags fuer Organisation (z.B. "faq", "regeln", "info")
- Berechtigungen: Nur Admins koennen Commands erstellen, alle koennen sie nutzen
- Persistierung in JSON oder SQLite (wenn F28 umgesetzt)

**Dashboard-Integration:**
- Custom-Commands im Dashboard verwalten (erstellen, bearbeiten, loeschen)
- Preview der Embed-Antwort vor dem Speichern

**Aufwand:** ~3-4 Stunden
**Dateien:** `cogs/custom_commands_cog.py` (neu), `data/custom_commands.json` (neu)
**Abhaengigkeiten:** Keine

### 31. Fail2Ban-Integration (P1)

Dashboard zeigt Fail2Ban-Status und erlaubt IP-Management. Bot meldet neue Bans.

**Funktionsumfang:**
- Dashboard-Seite: Aktive Bans, Ban-Historie, geblockte IPs mit Grund und Ablaufzeit
- Manuelles Entbannen per Dashboard-Button
- Bot-Benachrichtigung im Admin-Channel bei neuem Fail2Ban-Ban
- Statistiken: Bans pro Tag/Woche, haeufigste Angriffsarten (SSH, HTTP, etc.)
- Unified IP-Security-View: Fail2Ban + iptables Kicks/Bans + Blacklist an einem Ort

**Technische Umsetzung:**
- `fail2ban-client status` und `fail2ban-client status <jail>` fuer Daten
- Parsing der Fail2Ban-Logs fuer Historie
- Entbannen per `fail2ban-client set <jail> unbanip <ip>`
- Erfordert: sudoers-Eintrag fuer `fail2ban-client`

**Aufwand:** ~4-5 Stunden
**Dateien:** `web/routes/security_route.py` (neu), `modules/security/fail2ban.py` (neu)
**Abhaengigkeiten:** Fail2Ban auf dem Server installiert

### 32. SSL/Let's Encrypt Monitoring (P1)

Automatische Ueberwachung der SSL-Zertifikat-Gueltigkeit mit Warnung vor Ablauf.

**Funktionsumfang:**
- Taeglicher Check: Wann laeuft das Zertifikat ab?
- Warnung 14 Tage vorher im Admin-Channel + Dashboard-Banner
- Kritische Warnung 3 Tage vorher + E-Mail an Admin
- Dashboard-Widget zeigt Zertifikat-Status (gueltig bis, Tage verbleibend)
- Optional: Auto-Renewal Trigger per certbot

**Aufwand:** ~2 Stunden
**Dateien:** `modules/security/ssl_monitor.py` (neu), Integration in Monitor Bot
**Abhaengigkeiten:** certbot/Let's Encrypt auf dem Server

### 33. Backup-Integritaetscheck (P1)

Automatische Validierung jedes Backups nach Erstellung.

**Funktionsumfang:**
- Nach jedem Backup: SHA256-Checksum berechnen und speichern
- Archiv testweise entpacken (`tar -tzf`) um Korruptheit zu erkennen
- Groessen-Check: Warnung wenn Backup verdaechtig klein (< 50% der vorherigen Groesse)
- Groessen-Check: Warnung wenn Backup ungewoehnlich gross (> 200% der vorherigen Groesse)
- Checksum-Vergleich bei Restore (stimmt das Backup noch?)
- Dashboard: Backup-Health-Uebersicht (letzte 10 Backups mit Status)
- Benachrichtigung bei fehlgeschlagenem Integritaetscheck

**Aufwand:** ~3 Stunden
**Dateien:** Erweiterung `modules/backup/backup_manager.py`, neues Modul `modules/backup/integrity.py`
**Abhaengigkeiten:** Keine

### 34. Health-Endpoint fuer externes Monitoring (P1)

Oeffentlicher API-Endpoint fuer externe Monitoring-Tools (UptimeRobot, Hettzner, etc.).

**Funktionsumfang:**
- `GET /api/health` — Gibt JSON mit Server-Status zurueck
- Kein Auth noetig (nur Lese-Zugriff auf Status)
- Response: `{"status": "ok", "servers": {...}, "uptime": "99.5%", "timestamp": "..."}`
- HTTP 200 = alles OK, HTTP 503 = mindestens ein Server down
- Optional: `/api/health/<server_id>` fuer einzelne Server
- Rate-Limiting: Max. 60 Requests/Minute pro IP

**Aufwand:** ~2 Stunden
**Dateien:** `web/routes/health_route.py` (neu)
**Abhaengigkeiten:** Keine

### 35. Bot-Restart per Dashboard (P2)

Einzelne Bots (GameServer, Monitor, Admin) direkt im Dashboard neustarten.

**Funktionsumfang:**
- System-Seite im Dashboard: Restart-Button pro Bot-Service
- Bestaetigung-Dialog vor Restart
- Live-Feedback: "Restarting..." → "Online" (via SSE wenn F29 umgesetzt)
- Audit-Log-Eintrag bei jedem Restart
- Nur fuer eingeloggte Admins

**Technische Umsetzung:**
- `sudo systemctl restart <bot-service>` (sudoers bereits vorhanden)
- Achtung: Monitor Bot Restart → SSE/StatusWriter kurz unterbrochen

**Aufwand:** ~2 Stunden
**Dateien:** Erweiterung `web/routes/system_route.py`
**Abhaengigkeiten:** Keine (sudoers schon eingerichtet)

### 36. Offsite-Backup-Status im Dashboard (P2)

Zeigt OneDrive-Sync-Status und Cloud-Backup-Gesundheit im Dashboard.

**Funktionsumfang:**
- Dashboard-Widget: Letztes erfolgreiches Cloud-Backup (Zeitpunkt, Groesse)
- Warnung wenn letztes Cloud-Backup > 24h her
- OneDrive-Speicherverbrauch (falls API verfuegbar, sonst aus rclone Logs)
- Liste der letzten 10 Cloud-Backups mit Status (OK/Fehler)
- Sync-Fehler werden im Event-Log angezeigt

**Aufwand:** ~3 Stunden
**Dateien:** `modules/backup/onedrive_status.py` (neu), Erweiterung Dashboard
**Abhaengigkeiten:** rclone (bereits installiert)

### 37. Ressourcen-Forecasting (P2)

Vorhersage wann Serverressourcen erschoepft sind basierend auf historischen Daten.

**Funktionsumfang:**
- Dashboard-Widget: "Disk voll in ~X Tagen" basierend auf Wachstumstrend
- Berechnung via lineare Regression ueber die letzten 30 Tage stats_history
- Warnung wenn Disk < 20% frei ODER < 30 Tage bis voll
- Gleiche Logik fuer RAM-Trend (falls Savegames stetig wachsen)
- Admin-Benachrichtigung bei kritischer Prognose

**Aufwand:** ~3 Stunden
**Dateien:** `modules/monitoring/forecasting.py` (neu), Dashboard-Widget
**Abhaengigkeiten:** numpy (optional, sonst eigene lineare Regression)

### 38. Config Hot-Reload (P2)

Config-Aenderungen im Dashboard werden live uebernommen ohne Bot-Neustart.

**Funktionsumfang:**
- Dashboard Config-Seite: "Speichern + Anwenden" Button (statt nur "Speichern")
- Bot empfaengt Signal (z.B. via Datei-Watcher oder API-Call) und laedt Config neu
- Unterstuetzte Hot-Reload-Bereiche: Thresholds, Scheduler-Zeiten, Notification-Settings
- NICHT hot-reloadbar: Bot-Tokens, Discord-Channel-IDs (erfordern Reconnect)
- Feedback im Dashboard: "Config erfolgreich angewendet" oder "Neustart erforderlich fuer X"

**Technische Umsetzung:**
- FileWatcher auf `config/config.json` oder API-Endpoint `/api/config/reload`
- Bots registrieren Callback-Funktionen die bei Reload ausgefuehrt werden
- Graceful: Laufende Tasks werden nicht unterbrochen

**Aufwand:** ~4 Stunden
**Dateien:** `modules/config_reloader.py` (neu), Anpassung `utils/config.py`, Dashboard
**Abhaengigkeiten:** watchdog (optional, sonst mtime-basiert)

### 39. Spieler-Statistik-Profil (P2)

Discord-Command zeigt detailliertes Spieler-Profil als Embed.

**Funktionsumfang:**
- `/profil [spieler]` — Zeigt Profil-Embed mit:
  - Gesamt-Spielzeit (formatiert: Xd Xh Xm)
  - Sessions letzte 7/30 Tage
  - Lieblings-Server (meiste Spielzeit)
  - Erster und letzter Login
  - Aktuelle Session (falls online)
  - Warn-Anzahl (falls Warn-System aktiv)
  - Rang im Spielzeit-Leaderboard
- `/leaderboard [zeitraum]` — Top 10 Spieler nach Spielzeit (Woche/Monat/Gesamt)
- Daten kommen aus bestehendem PlayerTracker

**Aufwand:** ~3 Stunden
**Dateien:** `cogs/profile_cog.py` (neu)
**Abhaengigkeiten:** Bestehender PlayerTracker

### 40. Spieler-Benachrichtigungen Opt-in (P2)

Spieler melden sich per Command an und bekommen DMs bei Server-Events.

**Funktionsumfang:**
- `/notify subscribe <server>` — Benachrichtigung wenn Server online geht
- `/notify unsubscribe <server>` — Abmelden
- `/notify list` — Eigene Abonnements anzeigen
- Events: Server online, Server offline, Update verfuegbar, geplanter Restart
- Opt-in: Spieler muessen sich aktiv anmelden (kein Spam)
- Cooldown: Max. 1 DM pro Event (kein Flooding bei Flapping)
- Persistierung in JSON/SQLite

**Aufwand:** ~3 Stunden
**Dateien:** `cogs/notify_cog.py` (neu), `data/notify_subscriptions.json` (neu)
**Abhaengigkeiten:** Keine

### 41. Willkommens-System (P2)

Automatische Begruessung neuer Discord-Mitglieder.

**Funktionsumfang:**
- Embed-Nachricht im Willkommens-Channel bei Member-Join
- Konfigurierbarer Text mit Variablen: `{user}`, `{server}`, `{membercount}`
- Auto-Rolle: Neue Mitglieder bekommen automatisch eine Basis-Rolle
- Optional: DM an neues Mitglied mit Regeln/Info
- Konfigurierbar im Dashboard (Channel, Text, Rolle, DM an/aus)

**Aufwand:** ~2 Stunden
**Dateien:** `cogs/welcome_cog.py` (neu)
**Abhaengigkeiten:** Keine

### 42. Automatischer Paket-Update-Check (P2)

Woechentlicher Check auf verfuegbare System-Updates mit Dashboard-Anzeige.

**Funktionsumfang:**
- Woechentlicher `apt update && apt list --upgradable` Check
- Dashboard-Widget: X Updates verfuegbar (davon Y Security-Updates)
- Admin-Benachrichtigung bei Security-Updates
- Dashboard-Button: "Updates installieren" (mit Bestaetigung)
- Historie: Wann wurden zuletzt Updates installiert?

**Aufwand:** ~2-3 Stunden
**Dateien:** Erweiterung `web/routes/system_route.py`, `modules/system/package_manager.py` (neu)
**Abhaengigkeiten:** Keine (apt bereits vorhanden, sudoers teilweise eingerichtet)

### 43. Wartungsmodus-Toggle (P2)

Zentraler Schalter der alle Server als "Wartung" markiert.

**Funktionsumfang:**
- Dashboard-Toggle: "Wartungsmodus aktivieren/deaktivieren"
- Bei Aktivierung:
  - Alle Server-Kacheln zeigen "Wartung" statt "Online/Offline"
  - Discord-Nachricht im Info-Channel: "Server-Wartung in Kuerze..."
  - Geplante Restarts/Updates werden pausiert
  - Optional: Spieler X Minuten vorher warnen
- Bei Deaktivierung:
  - Normaler Status wird wieder angezeigt
  - Discord-Nachricht: "Wartung abgeschlossen, Server wieder verfuegbar"
- Slash-Command: `/wartung [an|aus] [grund]`
- Timer: Wartungsmodus endet automatisch nach X Stunden (Sicherheit)

**Aufwand:** ~3 Stunden
**Dateien:** Erweiterung StatusWriter, Dashboard, neuer Command
**Abhaengigkeiten:** Keine

### 44. IP-Security-Dashboard (P2)

Unified Ansicht aller IP-Sperren (Fail2Ban + iptables + Blacklist) an einem Ort.

**Funktionsumfang:**
- Dashboard-Seite "Sicherheit":
  - Aktive iptables REJECT-Regeln (Kicks/Bans)
  - Fail2Ban geblockte IPs (wenn F31 umgesetzt)
  - Blacklist-Eintraege (SAT + MC)
  - GeoIP-Info pro IP (Land, ISP) wenn moeglich
- Entbannen-Button pro IP (mit Bestaetigung)
- Statistik: Bans letzte 7 Tage, haeufigste IPs
- Abhaengig von F31 (Fail2Ban) fuer volle Funktionalitaet

**Aufwand:** ~4 Stunden (ohne F31), ~2 Stunden (als Erweiterung von F31)
**Dateien:** Erweiterung `web/routes/security_route.py`
**Abhaengigkeiten:** F31 (Fail2Ban) optional aber empfohlen

### 45. Changelog im Dashboard (P3)

Dashboard-Seite zeigt Aenderungshistorie nach Deployments.

**Funktionsumfang:**
- Dashboard-Seite `/changelog` liest `CHANGELOG.md` und rendert als HTML
- Aktuelle Version prominent angezeigt
- Filtermoeglich nach Version
- Optional: "Neu"-Badge im Dashboard-Menue nach Update

**Aufwand:** ~1-2 Stunden
**Dateien:** `web/routes/changelog_route.py` (neu)
**Abhaengigkeiten:** Keine

### 46. Dark Mode (P3)

Theme-Toggle fuer das Web-Dashboard.

**Funktionsumfang:**
- Toggle-Button in der Dashboard-Navigation (Sonne/Mond Icon)
- CSS-Variablen basiertes Theming (einfach umschaltbar)
- Praeferenz wird im Browser gespeichert (localStorage)
- Respektiert `prefers-color-scheme` als Standard
- Alle Dashboard-Seiten, Charts und Widgets unterstuetzen beide Themes

**Aufwand:** ~3-4 Stunden (CSS-Variablen fuer alle Seiten)
**Dateien:** `web/static/css/dark-theme.css` (neu), Anpassung `base.html`
**Abhaengigkeiten:** Keine

### 47. Auto-Deployment via GitHub Webhook (P3)

Automatisches Deployment bei Push auf den Main-Branch.

**Funktionsumfang:**
- GitHub Webhook empfaengt Push-Events
- Server fuehrt automatisch aus: `git pull` → `pip install -r requirements.txt` → Tests → Service-Restarts
- Dashboard zeigt Deployment-Status und -Historie
- Rollback-Button bei fehlgeschlagenem Deployment
- Nur Main-Branch, kein Deployment bei Feature-Branches
- Webhook-Secret fuer Sicherheit

**Voraussetzung:** Projekt muesste auf Git-basiertes Deployment umgestellt werden (aktuell SCP)

**Aufwand:** ~4-5 Stunden
**Dateien:** `web/routes/webhook_route.py` (neu), `scripts/auto_deploy.sh` (neu)
**Abhaengigkeiten:** Git auf dem Server, GitHub-Repository

### 48. Rate-Limiting fuer Dashboard-API (P2)

Schutz der Dashboard-Endpoints gegen Missbrauch.

**Funktionsumfang:**
- Rate-Limiting per IP: Max. X Requests/Minute pro Endpoint-Gruppe
- Strengere Limits fuer Actions (Restart, Kick, Ban) als fuer Lese-Endpoints
- Health-Endpoint (F34): 60 req/min
- Action-Endpoints: 10 req/min
- Login-Endpoint: 5 req/min (Brute-Force-Schutz)
- HTTP 429 Response bei Ueberschreitung
- Optional: IP-Whitelist fuer vertrauenswuerdige Quellen

**Aufwand:** ~2 Stunden
**Dateien:** `web/middleware/rate_limiter.py` (neu)
**Abhaengigkeiten:** Keine (eigene Implementierung mit Token-Bucket oder slowapi)

### 49. Disk-Space-Guard (P1)

Automatischer Notfall-Schutz wenn der Festplattenspeicher knapp wird.

**Funktionsumfang:**
- Background-Task prueft alle 10 Minuten den freien Speicherplatz
- Warnstufe 1 (< 20% frei): Admin-Benachrichtigung im Discord + Dashboard-Banner
- Warnstufe 2 (< 10% frei): Automatisch aelteste Backups + Logs loeschen
- Warnstufe 3 (< 5% frei): Kritische Warnung + E-Mail + optional Server stoppen
- Loesch-Reihenfolge: Alte Logs → Alte lokale Backups → Temp-Dateien
- Niemals aktive Savegames oder Config-Dateien loeschen
- Dashboard-Widget zeigt aktuelle Disk-Auslastung + Warnstufe
- Konfigurierbar: Schwellwerte, was geloescht werden darf, E-Mail an/aus

**Aufwand:** ~3 Stunden
**Dateien:** `modules/system/disk_guard.py` (neu), Integration in Monitor Bot
**Abhaengigkeiten:** Keine

### 50. Service-Watchdog fuer Bot-Prozesse (P1)

Ueberwacht die Bot-Services selbst (nicht nur Gameserver) und startet sie bei Absturz neu.

**Funktionsumfang:**
- Monitor Bot prueft alle 2 Minuten: Laeuft GameServer Bot? Laeuft Admin Bot?
- Bei Ausfall: Automatischer `systemctl restart` + Admin-Benachrichtigung
- Cooldown: Max. 3 Restarts pro Stunde pro Service
- Dashboard: Bot-Health-Status mit Restart-Historie
- Hinweis: Monitor Bot kann sich nicht selbst ueberwachen → systemd RestartPolicy nutzen

**Technische Umsetzung:**
- `systemctl is-active <service>` Check
- Restart per `sudo systemctl restart <service>` (sudoers vorhanden)
- Monitor Bot hat bereits `_check_service_active()` in StatusWriter — wiederverwenden

**Aufwand:** ~2 Stunden
**Dateien:** Erweiterung `bots/monitor_bot.py` oder neues Modul `modules/monitoring/service_watchdog.py`
**Abhaengigkeiten:** Keine

### 51. DuckDNS Auto-Update Check (P1)

Prueft ob die DuckDNS-Domain auf die richtige Server-IP zeigt.

**Funktionsumfang:**
- Taeglicher Check: DNS-Aufloesung von `marco-satisfactory.duckdns.org` → stimmt die IP?
- Vergleich mit tatsaechlicher Server-IP (via `ip addr` oder externer Service)
- Bei Abweichung: Kritische Warnung im Admin-Channel + E-Mail
- Optional: Automatisches DuckDNS-Update per API-Token
- Dashboard-Widget: Domain-Status (IP stimmt / IP weicht ab)

**Aufwand:** ~1-2 Stunden
**Dateien:** `modules/network/duckdns_monitor.py` (neu)
**Abhaengigkeiten:** Keine (DNS-Lookup via socket, IP-Check via HTTP)

### 52. Port-Monitoring (P1)

Prueft ob alle wichtigen Ports offen und erreichbar sind.

**Funktionsumfang:**
- Regelmaessiger Check (alle 5 Minuten): Sind folgende Ports erreichbar?
  - 7777 (SAT Game), 15000 (SAT API), 15777 (SAT Query)
  - 25565 (MC Vanilla), 25566 (MC BMC), 25575/25576 (RCON)
  - 8080 (Dashboard)
- Check-Methode: TCP-Connect-Test (bzw. UDP fuer Gameports wo moeglich)
- Bei geschlossenem Port: Warnung — "Port 7777 nicht erreichbar, UFW-Problem?"
- Dashboard: Port-Status-Uebersicht (gruen/rot pro Port)
- Erkennt UFW-Aenderungen oder Firewall-Probleme sofort

**Aufwand:** ~2-3 Stunden
**Dateien:** `modules/network/port_monitor.py` (neu)
**Abhaengigkeiten:** Keine

### 53. Stats-History Cleanup (P2)

Einmaliger + periodischer Cleanup der stats_history.json fuer saubere Chart-Daten.

**Funktionsumfang:**
- Einmaliger Cleanup: Alte Eintraege mit "unknown"-Status oder komplett 0-Werten entfernen
- Periodisch (woechentlich): Eintraege aelter als 90 Tage aggregieren (Stundenmittelwerte statt 5-Min-Intervalle)
- Ergebnis: Kleinere Datei, schnelleres Dashboard, saubere Charts
- Wird obsolet wenn F28 (SQLite) umgesetzt — dort ueber SQL-Retention geloest

**Aufwand:** ~1-2 Stunden
**Dateien:** `scripts/cleanup_stats.py` (neu) oder Integration in StatsCollector
**Abhaengigkeiten:** Keine

### 54. Geplanter Shutdown mit Countdown (P2)

Zeitgesteuertes Herunterfahren einzelner Server mit Spieler-Vorwarnung.

**Funktionsumfang:**
- `/shutdown <server> <minuten> [grund]` — Plant Shutdown in X Minuten
- Countdown-Warnungen im Discord: 30min, 15min, 5min, 1min vor Shutdown
- In-Game-Warnung (SAT via API Message, MC via RCON say)
- `/shutdown cancel` — Geplanten Shutdown abbrechen
- Dashboard: Geplante Shutdowns anzeigen + Cancel-Button
- Unterschied zu Wartungsmodus (F43): Shutdown ist zeitgesteuert fuer einzelne Server, Wartungsmodus ist sofort fuer alle

**Aufwand:** ~3 Stunden
**Dateien:** `cogs/shutdown_cog.py` (neu) oder Erweiterung bestehender Cogs
**Abhaengigkeiten:** Keine

### 55. Dashboard-Volltextsuche (P2, benoetigt F28)

Zentrale Suchfunktion im Dashboard ueber alle Daten.

**Funktionsumfang:**
- Suchfeld in der Dashboard-Navigation
- Durchsucht: Events, Spielernamen, Audit-Log, Commands, Backup-Namen
- Ergebnisse gruppiert nach Kategorie mit Direktlinks
- SQLite FTS5 (Full-Text Search) fuer schnelle Suche auch bei grossen Datenmengen
- Suchhistorie (letzte 5 Suchen)

**Aufwand:** ~3 Stunden
**Dateien:** Erweiterung `web/routes/`, neues Template, SQLite FTS5 Index
**Abhaengigkeiten:** F28 (SQLite)

### 56. Automatische Daten-Retention (P2, benoetigt F28)

Konfigurierbare Aufbewahrungsfristen pro Datentyp mit automatischer Bereinigung.

**Funktionsumfang:**
- Pro Tabelle konfigurierbar: Wie lange werden Daten aufbewahrt?
  - Stats-History: 90 Tage (Standard)
  - Events: 30 Tage
  - Audit-Log: 365 Tage
  - Player-Sessions: unbegrenzt
- Taeglicher Cleanup-Job: `DELETE FROM x WHERE timestamp < retention_date`
- Dashboard-Seite: Aktuelle Datenbankgroesse, Eintraege pro Tabelle, naechster Cleanup
- Konfigurierbar im Dashboard oder config.json

**Aufwand:** ~2 Stunden (als Teil von F28)
**Dateien:** Integration in `modules/database/`
**Abhaengigkeiten:** F28 (SQLite)

### 57. Erweiterte Dashboard-Analytics (P2, benoetigt F28)

Komplexe Auswertungen und Visualisierungen dank SQL-Abfragen.

**Funktionsumfang:**
- Heatmap: Zu welcher Uhrzeit sind die meisten Spieler online? (Stunde × Wochentag)
- Peak-Analyse: Wann war der Server am vollsten? (historisch)
- Trend-Vergleich: Diese Woche vs. letzte Woche (Spieleranzahl, Uptime, Crashes)
- Server-Vergleich: SAT vs. MC Nutzung nebeneinander
- Alle Queries als SQL statt Python-Loops → performant auch bei grossen Datenmengen

**Aufwand:** ~4-5 Stunden
**Dateien:** Erweiterung `web/routes/analytics_route.py`, neue Chart-Templates
**Abhaengigkeiten:** F28 (SQLite)

### 58. Daten-Export als CSV (P2, benoetigt F28)

Export von Dashboard-Daten fuer eigene Auswertungen.

**Funktionsumfang:**
- Download-Button pro Dashboard-Sektion: "Als CSV exportieren"
- Exportierbar: Spieler-Sessions, Server-Uptime-Historie, Event-Log, Backup-Historie
- Zeitraum waehlbar (letzte 7/30/90 Tage oder benutzerdefiniert)
- Direkt aus SQLite → CSV ohne Umweg ueber Python-Objekte

**Aufwand:** ~2 Stunden
**Dateien:** `web/routes/export_route.py` (neu)
**Abhaengigkeiten:** F28 (SQLite)

### 59. Command-Nutzungsstatistik (P2, benoetigt F28)

Tracking welche Discord-Commands wie oft genutzt werden.

**Funktionsumfang:**
- Automatisches Logging jeder Command-Ausfuehrung (Command-Name, User, Zeitpunkt, Server)
- Dashboard-Seite: Top 10 meistgenutzte Commands, Nutzung pro Tag/Woche
- Erkennung ungenutzter Commands ("Dieses Command wurde seit 30 Tagen nicht genutzt")
- Pro-User-Statistik: Wer nutzt welche Commands am meisten?
- Hilft bei Entscheidungen: Was brauchen die Spieler wirklich?

**Aufwand:** ~3 Stunden
**Dateien:** Erweiterung `modules/command_logger.py`, neues Dashboard-Template
**Abhaengigkeiten:** F28 (SQLite)

### 60. Korrelations-Analyse (P3, benoetigt F28)

Automatische Erkennung von Zusammenhaengen zwischen Server-Metriken.

**Funktionsumfang:**
- Analyse: "Crasht der Server haeufiger bei > X Spielern?"
- Analyse: "Steigt RAM-Verbrauch linear mit Spielzeit?"
- Analyse: "Gibt es Muster bei Server-Abstuerzen? (Uhrzeit, Wochentag, Spieleranzahl)"
- SQL JOINs ueber Stats + Events + Sessions Tabellen
- Dashboard: Einfache Korrelations-Grafiken (Scatter-Plots)
- Automatische Anomalie-Erkennung: "Ungewoehnlich hoher RAM-Verbrauch fuer diese Spieleranzahl"

**Aufwand:** ~5-6 Stunden
**Dateien:** `modules/analytics/correlation.py` (neu), Dashboard-Template
**Abhaengigkeiten:** F28 (SQLite), F57 (Erweiterte Analytics)

### 61. Graceful Shutdown Handler (P1)

Sauberes Herunterfahren der Bots bei `systemctl stop` oder SIGTERM.

**Problem:**
- Bei `systemctl restart` oder `systemctl stop` wird der Bot-Prozess abrupt beendet
- Offene DB-Connections werden nicht sauber geschlossen → potenzielle Korruption
- Laufende Background-Tasks (StatsCollector, HealthChecker, etc.) werden mittendrin abgebrochen
- Offene iptables-Operationen koennten halb-ausgefuehrt bleiben

**Loesung:**
- Signal-Handler fuer SIGTERM und SIGINT registrieren
- Bei Signal: Geordnetes Herunterfahren in definierter Reihenfolge:
  1. Neue Requests ablehnen / Marker setzen: "shutting_down = True"
  2. Laufende Background-Tasks sauber beenden (cancel + await)
  3. StatsCollector: Letzte Daten noch speichern
  4. HealthChecker: Laufende Checks abbrechen, Status zuruecksetzen
  5. SQLite: WAL-Checkpoint ausfuehren (`PRAGMA wal_checkpoint(TRUNCATE)`)
  6. DB-Connection sauber schliessen
  7. Log: "Bot sauber heruntergefahren"
- Timeout: Wenn nach 15 Sekunden nicht fertig → forciertes Beenden
- Status-Nachricht im Admin-Channel: "Bot wird heruntergefahren..."

**Aufwand:** ~2-3 Stunden
**Dateien:** `utils/shutdown.py` (neu), Integration in alle 3 `bots/*.py`
**Abhaengigkeiten:** F28 (SQLite) — ohne SQLite nur Background-Task-Cleanup

### 62. Startup Selftest (P1)

Automatische Pruefung beim Bot-Start ob alles korrekt konfiguriert ist.

**Problem:**
- Bot startet, aber ENV-Variablen fehlen → Crash erst bei erstem Command
- SQLite-Datenbank korrupt → Crash erst bei erstem DB-Zugriff
- Falscher Pfad in Config → Backup-System stumm kaputt
- Fehlende Permissions (sudoers, Verzeichnisrechte) → Fehler erst bei Nutzung

**Loesung — Checks beim Start (vor Bot.run()):**
1. **Config-Check:** config.json lesbar + valides JSON? Alle Pflicht-Keys vorhanden?
2. **ENV-Check:** Alle kritischen ENV-Variablen gesetzt? (BOT_TOKEN, GUILD_ID, etc.)
3. **DB-Check:** Datenbank oeffnen, `PRAGMA integrity_check`, Schema-Version pruefen
4. **Pfad-Check:** Alle konfigurierten Pfade existieren + Schreibrechte (data/, backups/, etc.)
5. **Permission-Check:** `sudo systemctl status` ausfuehrbar? (fuer Server-Steuerung)
6. **Network-Check:** DNS-Aufloesung funktioniert? Discord-API erreichbar?
7. **Dependency-Check:** Alle importierten Module verfuegbar? (aiosqlite, psutil, etc.)

**Verhalten bei Fehlern:**
- Kritische Fehler (DB korrupt, Token fehlt): Bot startet NICHT, gibt klare Fehlermeldung
- Warnungen (Pfad fehlt, optionale ENV fehlt): Bot startet, loggt Warnung, meldet im Admin-Channel
- Alle Check-Ergebnisse in Log-Datei schreiben fuer Debugging

**Dashboard-Integration:**
- Selftest-Ergebnis als JSON abrufbar (`/api/health/selftest`)
- Letzte Selftest-Ergebnisse auf System-Seite anzeigen

**Aufwand:** ~3-4 Stunden
**Dateien:** `utils/selftest.py` (neu), Integration in alle 3 `bots/*.py`
**Abhaengigkeiten:** F28 (SQLite) fuer DB-Check, ansonsten keine

### 63. SQLite-Backup-Strategie (P1, benoetigt F28)

Dedizierte Backup-Rotation fuer die SQLite-Datenbank — getrennt von Gameserver-Backups.

**Problem:**
- Nach dem SQLite-Umbau (F28) liegen ALLE persistenten Daten in `data/botdata.db`
- Spieler-Stats, Bans, Warns, Leveling, Tickets, Audit-Log — alles in einer Datei
- Wenn diese Datei korrupt wird oder versehentlich geloescht wird, sind ALLE Daten weg
- Gameserver-Backups sichern nur Savegames, nicht die Bot-Datenbank

**Loesung:**
- Eigene Backup-Rotation fuer `botdata.db`, getrennt von Gameserver-Backups
- Methode: `sqlite3 botdata.db ".backup data/backups/db/botdata_YYYYMMDD_HHMMSS.db"` — sicher im laufenden Betrieb (WAL-kompatibel)
- Alternativ: SQLite Online-Backup-API via `aiosqlite` / `connection.backup()`
- Rotation: 24 stuendliche Backups + 7 taegliche + 4 woechentliche (wie Gameserver-Backups)
- Integritaetscheck nach Backup: `PRAGMA integrity_check` auf die Backup-Datei
- Backup-Groesse und -Status in `backup_history` Tabelle loggen
- Dashboard: DB-Backup-Status auf System-Seite anzeigen
- Alert bei fehlgeschlagenem DB-Backup

**Aufwand:** ~2-3 Stunden
**Dateien:** Erweiterung `modules/backup/backup_manager.py`, Scheduler-Integration
**Abhaengigkeiten:** F28 (SQLite)

### 64. CSRF-Schutz fuer Dashboard (P1)

Cross-Site Request Forgery Schutz fuer alle Dashboard-Formulare und API-Endpoints.

**Problem:**
- Das Dashboard ermoeglicht Server-Restarts, Config-Aenderungen, Kick/Ban-Aktionen
- Ohne CSRF-Schutz koennte ein Angreifer Marco dazu bringen, einen manipulierten Link zu oeffnen
- Dieser Link koennte im Hintergrund Requests ans Dashboard senden (z.B. Server-Restart, Config aendern)
- Besonders kritisch weil das Dashboard auf einer oeffentlichen Domain laeuft (DuckDNS)

**Loesung:**
- CSRF-Token-Middleware fuer FastAPI
- Jedes Formular bekommt ein verstecktes `csrf_token` Feld
- Token wird serverseitig generiert und in der Session gespeichert
- Bei jedem POST/PUT/DELETE: Token validieren, bei Mismatch → 403 Forbidden
- HTMX-Requests: Token als Header `X-CSRF-Token` mitsenden (via `hx-headers`)
- Token-Rotation: Neues Token pro Session oder alle 30 Minuten
- Library: `starlette-csrf` oder eigene Middleware (~50 Zeilen)

**Aufwand:** ~1-2 Stunden
**Dateien:** `web/middleware/csrf.py` (neu), Anpassung aller Templates (hidden input), `web/app.py` (Middleware registrieren)
**Abhaengigkeiten:** Keine

### 65. Dashboard Session-Timeout (P1)

Automatischer Logout nach Inaktivitaet fuer das Web-Dashboard.

**Problem:**
- Aktuell bleibt der Admin-Login unbegrenzt aktiv
- Wenn Marco eingeloggt ist und den Browser offen laesst, hat jeder mit Zugang zum PC vollen Admin-Zugriff
- Besonders kritisch bei Zugriff von unterwegs oder geteilten Geraeten

**Loesung:**
- Session-Timeout nach 60 Minuten Inaktivitaet (konfigurierbar)
- Bei jedem Request: `last_activity` Timestamp in Session aktualisieren
- Middleware prueft: `now - last_activity > timeout` → automatischer Logout + Redirect zu Login
- 5-Minuten-Warnung vor Ablauf (via JavaScript-Timer im Frontend)
- "Angemeldet bleiben" Checkbox: Verlaengert Timeout auf 7 Tage (Cookie-basiert)
- Absoluter Timeout: Selbst bei Aktivitaet nach 24h neuer Login noetig
- Dashboard: Aktive Sessions anzeigen auf System-Seite

**Aufwand:** ~1-2 Stunden
**Dateien:** Erweiterung `web/auth.py`, `web/middleware/session_timeout.py` (neu), Anpassung `web/templates/base.html` (JS-Timer)
**Abhaengigkeiten:** Keine

---

## Zusammenfassung

| # | Feature | Prio | Aufwand | Status |
|---|---------|------|---------|--------|
| **Sicherheit + Stabilitaet** | | | | |
| 27 | Gameserver Health-Check mit Auto-Restart | P1 | 3-4h | Geplant |
| 31 | Fail2Ban-Integration | P1 | 4-5h | Erledigt v4.0.0 |
| 32 | SSL/Let's Encrypt Monitoring | P1 | 2h | Erledigt v4.0.0 |
| 33 | Backup-Integritaetscheck | P1 | 3h | Erledigt v4.0.0 |
| 34 | Health-Endpoint (externes Monitoring) | P1 | 2h | Erledigt v4.0.0 |
| 48 | Rate-Limiting Dashboard-API | P2 | 2h | Erledigt v4.0.0 |
| 49 | Disk-Space-Guard | P1 | 3h | Geplant |
| 50 | Service-Watchdog fuer Bot-Prozesse | P1 | 2h | Geplant |
| 51 | DuckDNS Auto-Update Check | P1 | 1-2h | Geplant |
| 52 | Port-Monitoring | P1 | 2-3h | Geplant |
| 61 | Graceful Shutdown Handler | P1 | 2-3h | Erledigt v4.0.0 |
| 62 | Startup Selftest | P1 | 3-4h | Erledigt v4.0.0 |
| 63 | SQLite-Backup-Strategie | P1 | 2-3h | Erledigt v4.0.0 |
| 64 | CSRF-Schutz Dashboard | P1 | 1-2h | Erledigt v4.0.0 |
| 65 | Dashboard Session-Timeout | P1 | 1-2h | Erledigt v4.0.0 |
| **Dashboard** | | | | |
| 29 | Live-Updates via SSE | P2 | 4-5h | Erledigt v4.0.0 |
| 35 | Bot-Restart per Dashboard | P2 | 2h | Erledigt v4.0.0 |
| 36 | Offsite-Backup-Status | P2 | 3h | Erledigt v4.0.0 |
| 37 | Ressourcen-Forecasting | P2 | 3h | Erledigt v4.0.0 |
| 44 | IP-Security-Dashboard | P2 | 2-4h | Erledigt v4.0.0 |
| 45 | Changelog im Dashboard | P3 | 1-2h | Erledigt v4.0.0 |
| 46 | Dark/Light Theme Toggle | P3 | 3-4h | Erledigt v4.0.0 |
| **Bot-Features** | | | | |
| 30 | Custom-Commands System | P2 | 3-4h | Erledigt v4.0.0 |
| 39 | Spieler-Statistik-Profil + Leaderboard | P2 | 3h | Erledigt v4.0.0 |
| 40 | Spieler-Benachrichtigungen Opt-in | P2 | 3h | Erledigt v4.0.0 |
| 41 | Willkommens-System | P2 | 2h | Erledigt v4.0.0 |
| 43 | Wartungsmodus-Toggle | P2 | 3h | Erledigt v4.0.0 |
| 54 | Geplanter Shutdown mit Countdown | P2 | 3h | Erledigt v4.0.0 |
| **Infrastruktur** | | | | |
| 28 | SQLite Datenbank-Upgrade (komplett) | P1 | 10-14h | Erledigt v4.0.0 |
| 38 | Config Hot-Reload | P2 | 4h | Erledigt v4.0.0 |
| 42 | Automatischer Paket-Update-Check | P2 | 2-3h | Geplant |
| 47 | Auto-Deployment via GitHub | P3 | 4-5h | Erledigt v4.0.0 |
| 53 | Stats-History Cleanup | P2 | 1-2h | Erledigt v4.0.0 |
| **SQLite-Bonus (benoetigen F28)** | | | | |
| 55 | Dashboard-Volltextsuche | P2 | 3h | Geplant |
| 56 | Automatische Daten-Retention | P2 | 2h | Erledigt v4.0.0 |
| 57 | Erweiterte Dashboard-Analytics | P2 | 4-5h | Erledigt v4.0.0 |
| 58 | Daten-Export als CSV | P2 | 2h | Erledigt v4.0.0 |
| 59 | Command-Nutzungsstatistik | P2 | 3h | Erledigt v4.0.0 |
| 60 | Korrelations-Analyse | P3 | 5-6h | Erledigt v4.0.0 |

**Erledigt:** 32/39 Features | **Noch offen:** F27, F42, F49, F50, F51, F52, F55 (7 Features)

---

## Erledigte Features

| # | Feature | Version | Phase |
|---|---------|---------|-------|
| 1 | Web-Status-Seite (statisch) | v3.1.0 | Phase 8g |
| 2 | Scheduled Messages | v3.1.0 | Phase 8f |
| 3 | Backup-Statistiken | v3.1.0 | Phase 8c |
| 4 | Server-Offline Decorator | v3.1.0 | Phase 8a |
| 6 | BMC Modpack-Updates | v3.1.0 | Phase 8h |
| 8 | Config-Backup Rotation + GPG | v3.1.0 | Phase 8d |
| 10 | MC Blacklist-System | v3.1.0 | Phase 8e |
| 11 | MC World-Analyse | v3.2.0 | Phase 10f |
| 12 | MC Autosave-Command | v3.1.0 | Phase 8b |
| 13 | Web-Dashboard (inkl. #14 Config-Panel) | v3.2.0 | Phase 13 |
| 14 | Discord-Bot-Konfiguration via Web-UI | v3.2.0 | Phase 13h |
| 16 | TeamSpeak-Integration (3 Phasen) | v3.2.0 | Phase 12b-d |
| 17 | Discord Temp Voice Channels | v3.2.0 | Phase 12a |
| 18 | Admin Bot (Moderation, Roles, Leveling, Tickets, Logging, Giveaways) | v3.2.0 | Phase 11a-h |
| 19 | Discord + TS Server-Backup (Struktur-Snapshot) | v3.2.0 | Phase 12e |
| 20 | SAT Auto-Update (sofort bei leerem Server + Spieler-Benachrichtigung) | v3.2.0 | Phase 10e |
| 21 | MC Ankuendigungs-Banner (/mc say + In-Game) | v3.2.0 | Phase 10b |
| 22 | MC Gameplay-Commands entfernen | v3.2.0 | Phase 10a |
| 23 | MC IP-Ban wie SAT (UFW-Firewall) | v3.2.0 | Phase 10c |
| 24 | Timeout-System (Temp-Ban alle Server + Restzeit + Channel) | v3.2.0 | Phase 10g |
| 25 | Command-Aufraeumung (Dashboard-Migration) | v3.2.0 | Phase 14 |
| 26 | Rollenbasierter Help-Befehl | v3.2.0 | Phase 10d |
| 28 | SQLite Datenbank-Upgrade | v4.0.0 | Phase 2 |
| 29 | Live-Updates via SSE | v4.0.0 | Phase 3 |
| 30 | Custom-Commands System | v4.0.0 | Phase 4 |
| 31 | Fail2Ban-Integration | v4.0.0 | Phase 1 |
| 32 | SSL/Let's Encrypt Monitoring | v4.0.0 | Phase 1 |
| 33 | Backup-Integritaetscheck | v4.0.0 | Phase 1 |
| 34 | Health-Endpoint | v4.0.0 | Phase 1 |
| 35 | Bot-Restart per Dashboard | v4.0.0 | Phase 3 |
| 36 | Offsite-Backup-Status | v4.0.0 | Phase 3 |
| 37 | Ressourcen-Forecasting | v4.0.0 | Phase 3 |
| 38 | Config Hot-Reload | v4.0.0 | Phase 5 |
| 39 | Spieler-Profil + Leaderboard | v4.0.0 | Phase 4 |
| 40 | Spieler-Benachrichtigungen | v4.0.0 | Phase 4 |
| 41 | Willkommens-System | v4.0.0 | Phase 4 |
| 43 | Wartungsmodus-Toggle | v4.0.0 | Phase 4 |
| 44 | IP-Security-Dashboard | v4.0.0 | Phase 3 |
| 45 | Changelog im Dashboard | v4.0.0 | Phase 5 |
| 46 | Dark/Light Theme Toggle | v4.0.0 | Phase 5 |
| 47 | Auto-Deployment via GitHub | v4.0.0 | Phase 5 |
| 48 | Rate-Limiting Dashboard-API | v4.0.0 | Phase 1 |
| 53 | Stats-History Cleanup | v4.0.0 | Phase 2 |
| 54 | Geplanter Shutdown mit Countdown | v4.0.0 | Phase 4 |
| 56 | Automatische Daten-Retention | v4.0.0 | Phase 2 |
| 57 | Erweiterte Dashboard-Analytics | v4.0.0 | Phase 3 |
| 58 | Daten-Export als CSV | v4.0.0 | Phase 3 |
| 59 | Command-Nutzungsstatistik | v4.0.0 | Phase 4 |
| 60 | Korrelations-Analyse | v4.0.0 | Phase 5 |
| 61 | Graceful Shutdown Handler | v4.0.0 | Phase 1 |
| 62 | Startup Selftest | v4.0.0 | Phase 1 |
| 63 | SQLite-Backup-Strategie | v4.0.0 | Phase 2 |
| 64 | CSRF-Schutz Dashboard | v4.0.0 | Phase 1 |
| 65 | Dashboard Session-Timeout | v4.0.0 | Phase 1 |

---

## Feature-Parity Uebersicht (SAT vs MC)

| Feature | SAT | MC | Status |
|---------|-----|----|----|
| Server-Steuerung (start/stop/restart) | Ja | Ja | Parity |
| Spieler-Management (kick/ban) | Ja (IP-basiert) | Ja (Name + IP) | Parity |
| Backup create/list/restore/download | Ja | Ja | Parity |
| Config backup/restore | Ja | Ja | Parity |
| Whitelist | Ja | Ja | Parity |
| Blacklist (eigenes System) | Ja | Ja | Parity |
| Blueprint-System | Ja | N/A | Nicht anwendbar fuer MC |
| Savegame/World-Statistiken | Ja (detailliert) | Ja (World-Analyse) | Parity |
| Update-Checker | Ja (SteamCMD) | Ja (Paper API + Modpack) | Parity |
| Auto-Update | Ja (bei leerem Server) | Nein (nur Benachrichtigung) | SAT-exklusiv |
| Settings-Management | Ja | Ja | Parity |
| Autosave-Command | Ja | Ja | Parity |
| Health-Check + Auto-Restart | Geplant (F27) | Geplant (F27) | Offen |
| SQLite Datenbank | Geplant (F28) | Geplant (F28) | Offen |
| Spieler-Profil + Leaderboard | Geplant (F39) | Geplant (F39) | Offen |
| Spieler-Benachrichtigungen | Geplant (F40) | Geplant (F40) | Offen |

---

## Empfohlene Reihenfolge

Basierend auf Abhaengigkeiten und Prioritaet. Reihenfolge kann durch optimierten Prompt angepasst werden.

**Phase 1 — Sicherheit + Stabilitaet (P1):**
F62 (Startup Selftest) → F61 (Graceful Shutdown) → F64 (CSRF-Schutz) → F65 (Session-Timeout) → F27 (Health-Check) → F49 (Disk-Guard) → F50 (Service-Watchdog) → F51 (DuckDNS) → F52 (Port-Monitor) → F31 (Fail2Ban) → F32 (SSL) → F33 (Backup-Integritaet) → F34 (Health-Endpoint) → F48 (Rate-Limiting)

**Phase 2 — Datenbank-Fundament:**
F28 (SQLite inkl. Config-Versioning, Alert-Dedup, Ban-Expiry) → F63 (SQLite-Backup) → F53 (Stats-Cleanup wird obsolet) → F56 (Retention) → F55 (Volltextsuche)

**Phase 3 — Dashboard-Upgrade:**
F29 (SSE Live-Updates) → F35 (Bot-Restart) → F36 (Offsite-Backup) → F37 (Forecasting) → F44 (IP-Security) → F57 (Erweiterte Analytics) → F58 (CSV-Export)

**Phase 4 — Bot-Features:**
F30 (Custom-Commands) → F39 (Spieler-Profil) → F40 (Benachrichtigungen) → F41 (Welcome) → F43 (Wartungsmodus) → F54 (Shutdown-Countdown) → F59 (Command-Statistik)

**Phase 5 — Infrastruktur + Komfort:**
F38 (Hot-Reload) → F42 (Paket-Updates) → F45 (Changelog) → F46 (Dark Mode) → F47 (Auto-Deploy) → F60 (Korrelation)

**Hinweis zu F28 (SQLite):** Sollte moeglichst frueh umgesetzt werden, da F55-F60 darauf aufbauen und viele andere Features (F30, F39, F40, F59) davon profitieren. Wenn F28 vor Phase 3+4 umgesetzt wird, koennen alle folgenden Features direkt SQLite nutzen statt JSON. F28 beinhaltet jetzt auch Config-Versioning (`config_history`), Alert-Deduplizierung (`alerts_sent`) und den Ban-Expiry Background-Task — diese sind integraler Bestandteil des SQLite-Umbaus.

**Hinweis zu F61+F62:** Selftest und Graceful Shutdown sollten VOR dem SQLite-Umbau implementiert werden (zumindest als Grundgeruest), damit der DB-Check sofort beim Start greift und die DB bei Restarts sauber geschlossen wird. Die SQLite-spezifischen Teile (WAL-Checkpoint, integrity_check) werden dann bei F28 ergaenzt.
