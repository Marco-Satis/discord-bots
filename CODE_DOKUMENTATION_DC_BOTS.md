# CODE_DOKUMENTATION_DC_BOTS

## Discord Bot System v4.1.0 — Vollstaendige Code-Dokumentation

> Automatisch generiert aus dem Quellcode am 07.04.2026

---

## Inhaltsverzeichnis

1. [Projektuebersicht](#1-projektuebersicht)
2. [Architektur und Ordnerstruktur](#2-architektur-und-ordnerstruktur)
3. [Die drei Bots — Einstiegspunkte](#3-die-drei-bots--einstiegspunkte)
4. [Cogs (Discord-Befehlsmodule)](#4-cogs-discord-befehlsmodule)
5. [Module — Kernlogik](#5-module--kernlogik)
6. [Web-Dashboard (FastAPI)](#6-web-dashboard-fastapi)
7. [Utilities](#7-utilities)
8. [Datenbank (SQLite)](#8-datenbank-sqlite)
9. [Konfiguration und Environment-Variablen](#9-konfiguration-und-environment-variablen)
10. [Scripts und Deployment](#10-scripts-und-deployment)
11. [Systemd-Services](#11-systemd-services)
12. [Tests](#12-tests)
13. [Abhaengigkeiten](#13-abhaengigkeiten)
14. [Patterns, Besonderheiten und Edge Cases](#14-patterns-besonderheiten-und-edge-cases)

---

## 1. Projektuebersicht

### Zweck

Ein 3-Bot-System mit Web-Dashboard zur Verwaltung von Game-Servern (Satisfactory + Minecraft) auf einem dedizierten Linux-Server. Das System umfasst Server-Steuerung, Monitoring, Moderation, Community-Features und automatisierte Wartung.

### Tech-Stack

- **Sprache:** Python 3.10
- **Discord-Framework:** discord.py 2.x (Slash Commands, Cog-System)
- **Web-Framework:** FastAPI + Uvicorn (ASGI)
- **Templates:** Jinja2 + HTMX
- **Datenbank:** aiosqlite (SQLite im WAL-Modus)
- **Frontend:** Chart.js, CSS Custom Properties, Server-Sent Events (SSE)
- **Reverse Proxy:** Nginx + Let's Encrypt
- **Prozessmanagement:** systemd
- **Auth:** Discord OAuth2 + JWT

### Unterstuetzte Game-Server

| Server | Typ | Steuerung | Chat Bridge |
|--------|-----|-----------|-------------|
| Satisfactory | Dedicated Server (HTTPS-API, SteamCMD) | HTTP API + systemd | — |
| MC Vanilla/Paper | Paper MC 1.21.4 | RCON + systemd | Log-Polling + RCON |
| MC Better MC (BMC5) | NeoForge Modpack 1.21.1 | RCON + systemd | Log-Polling + RCON |

### Hosting

Netcup RS 4000 G12: 12 vCores, 31 GB RAM, 1 TB NVMe, Ubuntu 22.04 LTS

### Port-Uebersicht

| Port | Protokoll | Dienst | Zugriff |
|------|-----------|--------|---------|
| 443 | TCP/HTTPS | Nginx -> Dashboard | Extern |
| 8080 | TCP/HTTP | Web Dashboard (uvicorn) | Localhost |
| 4422 | TCP | SSH | Extern |
| 7777 | TCP+UDP | Satisfactory Game | Extern |
| 15777 | UDP | Satisfactory Query | Extern |
| 25565 | TCP | MC Vanilla | Extern |
| 25566 | TCP | MC Better MC | Extern |
| 25575 | TCP | MC RCON (BMC) | Localhost |

---

## 2. Architektur und Ordnerstruktur

```
Discord_Bots/
|-- bots/                          # Bot-Einstiegspunkte (3 Bots)
|   |-- admin_bot.py               # Bot 3: Moderation, Community, TeamSpeak
|   |-- gameserver_bot.py          # Bot 1: Satisfactory + Minecraft Steuerung
|   |-- monitor_bot.py             # Bot 2: Health Monitoring, Scheduler, Alerts
|
|-- cogs/                          # Discord Slash-Command Module (28 Cogs)
|   |-- __init__.py
|   |-- audit_cog.py               # Audit-Logging fuer Discord-Events
|   |-- command_stats_cog.py       # Befehlsnutzungs-Statistiken
|   |-- custom_commands_cog.py     # Benutzerdefinierte Text-Commands
|   |-- embed_sender_cog.py       # Dashboard-Embed-Queue
|   |-- general_cog.py             # /help, /clear, /reload, /ping
|   |-- giveaway_cog.py            # Gewinnspiel-System
|   |-- leveling_cog.py            # XP/Level-System mit Rollenbelohnungen
|   |-- maintenance_cog.py         # Legacy-Platzhalter (leer)
|   |-- maintenance_mode_cog.py    # Globaler Wartungsmodus
|   |-- minecraft_cog.py           # /mc Commands (Multi-Server)
|   |-- mod_cog.py                 # Mod-Info Anzeige (SAT + MC)
|   |-- moderation_cog.py          # Wortfilter + Anti-Spam
|   |-- monitor_cog.py             # /performance, /stats, /report
|   |-- notify_cog.py              # Spieler-Benachrichtigungen (Opt-in)
|   |-- profile_cog.py             # Spieler-Profile + Leaderboard
|   |-- reaction_roles_cog.py      # Selbstzuweisbare Rollen via Emoji
|   |-- satisfactory_cog.py        # /sat Commands (Server-Steuerung)
|   |-- scheduler_cog.py           # Zentral-Scheduler (alle periodischen Tasks)
|   |-- server_backup_cog.py       # Discord-Server Struktur-Backup
|   |-- shutdown_cog.py            # Geplantes Herunterfahren mit Countdown
|   |-- teamspeak_cog.py           # TeamSpeak-Verwaltung + Chat-Bridge
|   |-- temp_voice_cog.py          # Temporaere Voice-Channels
|   |-- tickets_cog.py             # Support-Ticket-System
|   |-- timeout_cog.py             # Multi-Server Temp-Bans
|   |-- update_cog.py              # MC Modpack + SAT Update-Commands
|   |-- warn_cog.py                # Verwarnungssystem
|   |-- welcome_cog.py             # Willkommensnachrichten + Auto-Rollen
|
|-- modules/                       # Kernlogik-Module
|   |-- alert_dedup.py             # Alert-Deduplizierung
|   |-- anti_spam.py               # Anti-Spam (Legacy)
|   |-- audit_logger.py            # Audit-Event-Logger
|   |-- command_logger.py          # Befehls-Audit-Trail
|   |-- config_history.py          # Konfigurations-Aenderungsverlauf
|   |-- config_reloader.py         # Hot-Reload fuer config.json
|   |-- config_validator.py        # Konfigurations-Validierung
|   |-- giveaways.py               # Gewinnspiel-Manager
|   |-- leveling.py                # XP/Level-Berechnung
|   |-- maintenance.py             # Bot-Wartungsstatus
|   |-- mod_manager.py             # Mod-Installation/Verwaltung
|   |-- restart_timer.py           # Countdown-Timer fuer Restarts
|   |-- server_backup.py           # Discord-Server-Backup-Manager
|   |-- temp_voice.py              # Temp-Voice-Channel-Logik
|   |-- temp_voice_views.py        # Discord UI-Views fuer Temp-Voice
|   |-- tickets.py                 # Ticket-System-Logik
|   |-- timeout_manager.py         # Timeout-Verwaltung
|   |-- warn_manager.py            # Verwarnungs-Verwaltung
|   |-- word_filter.py             # Wortfilter (Legacy)
|   |
|   |-- analytics/                 # Analyse-Subsystem
|   |   |-- correlation.py         # Pearson-Korrelation, Anomalie-Erkennung
|   |
|   |-- backup/                    # Backup-Subsystem
|   |   |-- backup_manager.py      # tar.gz Backup-Erstellung + Rotation
|   |   |-- config_backup.py       # Config-Dateien -> OneDrive
|   |   |-- integrity.py           # SHA256 Backup-Verifizierung
|   |   |-- onedrive_backup.py     # rclone Cloud-Upload
|   |   |-- onedrive_status.py     # OneDrive-Status-Abfrage
|   |
|   |-- database/                  # Datenbank-Layer
|   |   |-- db_manager.py          # Verbindungsmanager (WAL-Modus)
|   |   |-- json_importer.py       # JSON -> SQLite Migration
|   |   |-- maintenance.py         # Backup-Rotation, Retention
|   |   |-- migrations.py          # Schema-Versionierung (V1-V4)
|   |   |-- models.py              # 20 Dataclass-Modelle
|   |   |-- search_indexer.py      # FTS5 Volltextsuche
|   |
|   |-- minecraft/                 # Minecraft-Subsystem
|   |   |-- backup.py              # Welt-Backup-Manager
|   |   |-- blacklist.py           # Cross-Server Bans
|   |   |-- chat_bridge.py         # MC <-> Discord Chat-Bruecke
|   |   |-- file_manager.py        # Download, ZIP, Hash, Atomic Swap
|   |   |-- mc_countdown.py        # RCON /title Countdown-Timer
|   |   |-- modpack_updater.py     # CurseForge API Update-Check
|   |   |-- neoforge_updater.py    # NeoForge-Versionsmanagement
|   |   |-- rcon.py                # Async RCON-Protokoll-Client
|   |   |-- server.py              # MC-Server-Steuerung (systemd + RCON)
|   |   |-- settings_backup.py     # server.properties Backup
|   |   |-- update_checker.py      # Paper Update-Check via API
|   |   |-- update_manager.py      # 8-Phasen Update-Orchestrierung
|   |   |-- world_analyzer.py      # level.dat + Stats-Analyse (nbtlib)
|   |
|   |-- moderation/                # Discord-Moderations-Subsystem
|   |   |-- anti_spam.py           # Nachrichtenflut/Duplikat-Erkennung
|   |   |-- word_filter.py         # Regex-basierter Wortfilter
|   |
|   |-- monitoring/                # Server-Ueberwachung
|   |   |-- auto_cleanup.py        # Log/Cache-Rotation
|   |   |-- crash_replay.py        # Log-Kontext bei Crashes
|   |   |-- forecasting.py         # Lineare Regression (Disk/RAM)
|   |   |-- graceful_degradation.py # Reduzierter Betrieb bei Fehlern
|   |   |-- health_check.py        # Crash-Erkennung + Auto-Restart
|   |   |-- health_checker.py      # UDP/TCP Health-Probes
|   |   |-- login_audit.py         # Login-Anomalie-Erkennung
|   |   |-- optimizer.py           # Performance-Optimierungsvorschlaege
|   |   |-- performance.py         # CPU/RAM/Disk-Metriken (psutil)
|   |   |-- player_ip_tracker.py   # IP-basiertes Spieler-Tracking
|   |   |-- player_tracker.py      # Session-basiertes Spieler-Tracking
|   |   |-- savegame_protection.py # Savegame-Integritaetsschutz
|   |   |-- selftest.py            # Monitoring-spezifischer Selftest
|   |   |-- service_watchdog.py    # systemd-Service-Ueberwachung
|   |   |-- stats_collector.py     # Metriken -> SQLite (5min Intervall)
|   |   |-- stats_tracker.py       # Langzeit-Metriken
|   |   |-- status_writer.py       # Status-JSON fuer Dashboard
|   |   |-- steam_changelog.py     # Steam-Update-Changelog
|   |   |-- update_checker.py      # SteamCMD Build-ID-Check
|   |   |-- web_status.py          # Web-Status-Schnittstelle
|   |
|   |-- network/                   # Netzwerk-Monitoring
|   |   |-- duckdns_monitor.py     # DNS-Konsistenz-Pruefung
|   |   |-- port_monitor.py        # Port-Erreichbarkeits-Check
|   |
|   |-- notifications/             # Benachrichtigungen
|   |   |-- discord_notifier.py    # Discord-Embed-Benachrichtigungen
|   |   |-- email_notifier.py      # SMTP E-Mail-Alerts
|   |
|   |-- satisfactory/              # Satisfactory-Subsystem
|   |   |-- api_client.py          # HTTPS API Client (Self-Signed)
|   |   |-- blacklist.py           # Satisfactory-Banliste
|   |   |-- blueprint_manager.py   # Blueprint-Upload/Download
|   |   |-- save_header.py         # Binaerer Save-Header-Parser
|   |   |-- savegame_analyzer.py   # Tiefe Savegame-Analyse
|   |   |-- savegame_stats.py      # Savegame-Statistiken
|   |   |-- server.py              # SAT-Server-Steuerung (systemd)
|   |   |-- settings_backup.py     # API-Settings-Backup
|   |   |-- whitelist.py           # Whitelist-Manager
|   |
|   |-- security/                  # Sicherheits-Subsystem
|   |   |-- ban_manager.py         # IP-Ban via iptables
|   |   |-- fail2ban.py            # Fail2Ban-Integration
|   |   |-- ssl_monitor.py         # SSL-Zertifikats-Ueberwachung
|   |
|   |-- system/                    # System-Ueberwachung
|   |   |-- disk_guard.py          # 3-Stufen Speicherplatz-Warnung
|   |   |-- package_checker.py     # apt-Paket-Aktualisierungen
|   |
|   |-- teamspeak/                 # TeamSpeak-Integration
|       |-- channel_manager.py     # TS-Kanal-Verwaltung
|       |-- chat_bridge.py         # Discord <-> TS Chat-Bruecke
|       |-- ts_client.py           # TS3 ServerQuery Client
|
|-- web/                           # Web-Dashboard
|   |-- app.py                     # FastAPI-App mit Middleware-Stack
|   |-- auth.py                    # OAuth2 + JWT + Fallback-Auth
|   |-- middleware/
|   |   |-- csrf.py                # CSRF-Token-Schutz
|   |   |-- rate_limiter.py        # IP-basiertes Rate-Limiting
|   |   |-- session_timeout.py     # Session-Ablauf-Verwaltung
|   |-- routes/                    # 19 Route-Module
|   |   |-- admin_bot_route.py     # Admin-Bot Konfiguration
|   |   |-- analytics_route.py     # Heatmaps + Analytics
|   |   |-- backup_status_route.py # Backup-Status-Anzeige
|   |   |-- changelog_route.py     # CHANGELOG.md-Anzeige
|   |   |-- config_reload_route.py # Config Hot-Reload
|   |   |-- config_route.py        # Konfigurations-Editor
|   |   |-- correlation_route.py   # Korrelations-Dashboard
|   |   |-- dashboard.py           # Hauptuebersicht
|   |   |-- errors_route.py        # Error-Dashboard
|   |   |-- export_route.py        # CSV/JSON-Export
|   |   |-- forecast_route.py      # Ressourcen-Prognose
|   |   |-- health_route.py        # Health-API + Selftest
|   |   |-- search_route.py        # Volltextsuche
|   |   |-- security_route.py      # Security-Dashboard
|   |   |-- server_detail.py       # Server-Detail-Ansicht
|   |   |-- sse_route.py           # Server-Sent Events
|   |   |-- system_route.py        # System-Uebersicht
|   |   |-- theme_route.py         # Dark-Mode-Toggle
|   |   |-- webhook_route.py       # Webhook-Integration
|   |-- static/                    # CSS + JS
|   |   |-- htmx.min.js
|   |   |-- style.css
|   |   |-- themes.css
|   |-- templates/                 # Jinja2 HTML-Templates
|       |-- base.html, dashboard.html, login.html, ...
|       |-- partials/              # HTMX-Partial-Templates
|
|-- utils/                         # Gemeinsame Hilfsfunktionen
|   |-- config.py                  # ENV + config.json Loader
|   |-- formatting.py              # Uptime, Bytes, Progress-Bar
|   |-- logger.py                  # Rotating File Logger
|   |-- permissions.py             # Rollen-basierte Zugriffskontrolle
|   |-- selftest.py                # Pre-Boot-Verifikation
|   |-- shutdown.py                # Graceful Shutdown Handler
|
|-- config/                        # Konfiguration
|   |-- .env                       # Aktive Umgebungsvariablen (gitignored)
|   |-- .env.example               # Template fuer .env
|   |-- config.json                # Feature-Toggles + Schwellwerte
|
|-- scripts/                       # Deployment + Verwaltung
|   |-- auto_deploy.sh             # Automatisches Deployment
|   |-- bot_watchdog.sh            # Bot-Ueberwachungs-Script
|   |-- deploy.sh                  # Manuelles Deployment
|   |-- manage_bots.sh             # Bot-Verwaltung (start/stop/status)
|   |-- optimize_server.sh         # Server-Optimierung
|   |-- rcon_op.py                 # RCON OP-Script
|   |-- setup_minecraft.sh         # MC-Server-Setup
|   |-- setup_nginx.sh             # Nginx + SSL Setup
|
|-- systemd/                       # Service-Definitionen
|   |-- admin-bot.service
|   |-- gameserver-bot.service
|   |-- monitor-bot.service
|   |-- web-dashboard.service
|   |-- bot-watchdog.service
|   |-- bot-watchdog.timer
|
|-- tests/                         # Automatisierte Tests
|   |-- test_imports.py
|   |-- test_cogs.py
|   |-- test_env_completeness.py
|   |-- test_routes.py
|   |-- test_server_bots.py
|   |-- test_server_dashboard.py
|   |-- test_sat_status.py
|   |-- test_sat_debug.py
|   |-- test_server_update.py
|
|-- data/                          # Laufzeitdaten (pro Bot)
|   |-- admin/                     # Admin-Bot-Daten
|   |-- gameserver/                # GameServer-Bot-Daten
|   |-- monitor/                   # Monitor-Bot-Daten
|
|-- backups/                       # Lokale Backup-Ablage
|-- logs/                          # Log-Dateien (rotierend)
|-- docs/                          # Projektdokumentation
|-- VERSION                        # Aktuelle Version (4.1.0)
|-- CLAUDE.md                      # Entwicklungs-Anweisungen
|-- README.md                      # Projektuebersicht
|-- CHANGELOG.md                   # Versionshistorie
|-- PROGRESS.md                    # Upgrade-Fortschritt
|-- requirements.txt               # Python-Abhaengigkeiten
|-- .gitignore                     # Git-Ausschluesse
```

---

## 3. Die drei Bots — Einstiegspunkte

### 3.1 GameServer Bot (`bots/gameserver_bot.py`)

**Rolle:** Bot 1 — Satisfactory + Minecraft Server-Steuerung

**Initialisierung:**
- Laedt Satisfactory-Module: `SatisfactoryServer`, `SatisfactoryAPI`, `WhitelistManager`, `BlacklistManager`, `BlueprintManager`, `SavegameStats`
- Laedt optional Minecraft-Server basierend auf `MC_*_SERVICE` ENV-Variablen (BMC, VANILLA)
- Pro MC-Server: `BackupManager`, `ModManager`, `PlayerIPTracker`, `ModpackUpdater`
- Laedt Backup-, Monitoring- und Moderation-Module

**Geladene Cogs (5 + 1 optional):**
1. `satisfactory_cog` — Alle `/sat` Slash Commands
2. `general_cog` — `/help`, `/server`, `/ping`, `/reload`
3. `timeout_cog` — `/timeout` Moderation
4. `mod_cog` — Mod-Verwaltung
5. `maintenance_cog` — Bot-Wartung
6. `minecraft_cog` (bedingt) — Alle `/mc` Commands

**Hintergrund-Tasks:**
- Status-JSON-Writer (alle 30s) -> `data/gameserver/bot_status.json`
- Atomarer File-Write (Temp-Datei -> Rename)

**Besonderheiten:**
- Multi-Server-Architektur: Dynamische Minecraft-Server-Erkennung
- Command-Logger: Serialisiert Discord-Objekte (Attachment -> Filename, Member -> String)
- Presence: "Watching Satisfactory Server"
- Slash Command Sync: Sowohl Guild als auch Global

### 3.2 Monitor Bot (`bots/monitor_bot.py`)

**Rolle:** Bot 2 — Hintergrund-Ueberwachung und Automatisierung

**Kernsysteme:**
- Health Auto-Restart: UDP/TCP-Probes mit Crash-Erkennung
- Service Watchdog: systemd-Service-Status-Ueberwachung
- Disk Guard: 3-stufiges Speicherplatz-Warnsystem
- DuckDNS Monitor: DNS-Konsistenzpruefung (taeglich)
- Port Monitor: Offene Ports fuer Game-Server verifizieren
- SSL Monitor: Zertifikats-Ablauf-Tracking
- Player IP Tracker: Login-Audit fuer MC-Server
- Backup Integrity: Checksummen-Validierung
- Stats Collector: Metriken-Aggregation (5min Intervall)
- Crash Replay: Log-Kontext-Sicherung bei Crashes

**Hintergrund-Tasks (async loops):**
- `player_log_task()` — MC-Log-Parsing (Join/Leave/Advancement)
- `mc_chat_bridge_task()` — MC -> Discord Chat
- `health_check_task()` — SAT Health mit Crash-Erkennung
- `update_status_embed()` — Status-Embed alle 10min
- `login_audit_task()` — Login-Anomalie-Erkennung
- `ssl_check_task()` — Zertifikats-Pruefung
- `health_auto_restart_task()` — Automatische Crash-Recovery

**Event-Handler (15+):**
- `_on_crash()`, `_on_player_join()`, `_on_player_leave()`
- `_on_service_failed()`, `_on_service_recovered()`
- `_on_disk_critical()`, `_on_disk_warning()`
- `_on_duckdns_mismatch()`, `_on_port_closed()`

**Benachrichtigungssystem:**
- `DiscordNotifier`: Direkt an Admin-Channel + Rollen-Mention
- `EmailNotifier`: SMTP-Alerts (optional)
- Alert-Deduplizierung mit Cooldown

### 3.3 Admin Bot (`bots/admin_bot.py`)

**Rolle:** Bot 3 — Discord-Moderation, Community-Features, TeamSpeak

**Geladene Cogs (16):**
1. `moderation_cog` — Wortfilter + Spam-Erkennung
2. `warn_cog` — Verwarnungssystem
3. `reaction_roles_cog` — Rollen via Reaktionen
4. `leveling_cog` — XP/Level-System
5. `tickets_cog` — Support-Tickets
6. `audit_cog` — Audit-Logging
7. `giveaway_cog` — Gewinnspiele
8. `temp_voice_cog` — Temporaere Voice-Channels
9. `teamspeak_cog` — TeamSpeak-Integration
10. `server_backup_cog` — Discord-Server-Backup
11. `embed_sender_cog` — Dashboard-Embed-Queue
12. `custom_commands_cog` — Benutzerdefinierte Commands
13. `profile_cog` — Spieler-Profile + Leaderboard
14. `notify_cog` — Spieler-Benachrichtigungen
15. `welcome_cog` — Willkommenssystem
16. `command_stats_cog` — Befehlsstatistiken

**Besonderheiten:**
- AllowedMentions auf NONE (keine versehentlichen Pings)
- Permission-Hierarchie: Owner > Admin > Player > Everyone
- Token-basierte Aktivierung: Startet nicht ohne `ADMIN_BOT_TOKEN`
- DB-Init beim Setup-Hook

---

## 4. Cogs (Discord-Befehlsmodule)

### 4.1 satisfactory_cog.py (1696 Zeilen)

**Befehlsgruppe:** `/sat`

| Befehl | Beschreibung | Berechtigung |
|--------|-------------|--------------|
| `/sat status` | Server-Status mit API-State | Spieler |
| `/sat players online` | Online-Spieler anzeigen | Spieler |
| `/sat players ban <name>` | Spieler bannen (inkl. IP-Ban via UFW) | Admin |
| `/sat players unban <name>` | Spieler entbannen | Admin |
| `/sat players bans` | Banliste anzeigen | Admin |
| `/sat sav save` | Manuelles Speichern | Admin |
| `/sat sav download` | Savegame als ZIP herunterladen | Admin |
| `/sat sav upload` | Savegame hochladen | Owner |
| `/sat sav list` | Alle Savegames auflisten | Spieler |
| `/sat sav restore` | Savegame wiederherstellen (mit Backup) | Owner |
| `/sat sav load <name>` | Bestimmtes Save laden | Admin |
| `/sat sav stats` | Savegame-Statistiken | Spieler |
| `/sat config settings` | Server-Konfiguration (read-only) | Admin |
| `/sat blueprints upload/list/download/delete` | Blueprint-Verwaltung | Spieler/Admin |
| `/sat whitelist add/remove/list` | Whitelist-Verwaltung | Admin |
| `/sat blacklist add/remove/list` | Blacklist-Verwaltung | Admin |

**Implementierungsdetails:**
- IP-basierte Bans mit UFW-Regeln (`sudo ufw deny from <ip>`)
- Blueprint-Upload: Validiert sowohl .sbp+.sbpcfg als auch ZIP-Archive
- Savegame-Upload mit 500 MB Groessenlimit
- Automatisches Backup vor Restore
- Persistente Confirm-Views fuer Restore/Upload

### 4.2 minecraft_cog.py (1691 Zeilen)

**Befehlsgruppe:** `/mc`

| Befehl | Beschreibung | Berechtigung |
|--------|-------------|--------------|
| `/mc status [server]` | Server-Status (Multi-Server) | Spieler |
| `/mc players list/kick/ban/pardon [server]` | Spieler-Verwaltung | Spieler/Admin |
| `/mc backup create/list/restore/download [server]` | Welt-Backup | Admin |
| `/mc whitelist add/remove/list [server]` | Whitelist | Admin |
| `/mc blacklist add/remove/list/history` | Server-weite Bans | Admin |
| `/mc world stats [server]` | Welt-Analyse (level.dat) | Spieler |
| `/mc command <cmd> [server]` | RCON-Ausfuehrung | Owner |
| `/mc say [banner] [repeat]` | In-Game Ankuendigungen | Admin |
| `/mc config settings/stats/modpack_check` | Config (read-only) | Admin |

**Implementierungsdetails:**
- Multi-Server Autocomplete: Dynamisches `[server]`-Argument
- RCON-Input-Sanitization: Gefaehrliche Zeichen werden entfernt
- Blacklist: Cross-Server-Synchronisation via RCON
- IP-Ban Support via UFW
- Backup-Download als ZIP mit Discord-Groessenlimits
- Ankuendigungs-Repeat mit Countdown fuer Restart-Warnungen
- Auto-Save: Periodischer `save-all` RCON-Aufruf

### 4.3 scheduler_cog.py (2142 Zeilen — groesstes Cog)

**Rolle:** Zentraler Scheduler fuer ALLE periodischen Aufgaben

**Geplante Tasks:**

| Task | Intervall | Beschreibung |
|------|-----------|-------------|
| Auto-Backup (SAT) | 6 Stunden | Backup mit Cloud-Upload |
| Daily Restart (SAT) | 04:00 UTC | Min. 12h Uptime, Pre-Restart-Backup |
| Update Check (SAT) | 6 Stunden | SteamCMD Build-ID |
| Auto-Update Install | Bei leerem Server | Health-Check + Rollback |
| Config Backup | 03:00 UTC | Config -> OneDrive (GPG optional) |
| Auto-Cleanup | 02:00 UTC | Log/Cache-Rotation |
| MC Modpack Update | 12:00/00:00 | CurseForge API-Check |
| SAT Auto-Update | 12:00/00:00 | SteamCMD Update |
| MC Daily Restart | 05:00 UTC/Server | Pre-Restart-Backup optional |
| MC Auto-Backup | 6 Stunden/Server | Welt-Backup mit Rotation |
| MC Update Check | 6 Stunden/Server | Paper API Check |
| MC Config Backup | 03:00 UTC/Server | server.properties Backup |
| Scheduled Messages | Konfigurierbar | Phase 8f geplante Nachrichten |
| Retention Cleanup | 04:00 UTC | Rollback-Dirs + alte ZIPs |

**Minuten-basierte Scheduler-Loop:**
- Prueft jede 60 Sekunden gegen geplante Zeiten
- Health-Checker-Integration (Mindest-Uptime, Spieler-Online-Status)
- Rollback-System: Pre-Update-Backup -> Health-Check (3min) -> Revert bei Fehler
- Auto-Update-Cooldown: 30min zwischen Versuchen
- HAR-Unterdrueckung waehrend geplanter Operationen

### 4.4 monitor_cog.py (1762 Zeilen)

**Slash Commands:**

| Befehl | Beschreibung | Berechtigung |
|--------|-------------|--------------|
| `/performance` | System-Metriken (CPU/RAM/Disk + Prozesse) | Admin |
| `/dashboard` | Status-Embed manuell aktualisieren | Admin |
| `/stats [spieler]` | Spieler-Statistiken (einzeln oder alle) | Spieler |
| `/report [zeitraum]` | Aktivitaetsbericht mit Trends | Admin |
| `/mon world` | Welt-Statistiken (Gebaeude, Strom, Transport) | Spieler |
| `/selftest` | Alle Bot-System-Checks ausfuehren | Owner |
| `/commandlog [anzahl]` | Letzte Bot-Befehle anzeigen | Admin |

**Implementierungsdetails:**
- Echtzeit-Performance via psutil
- Trend-Analyse: Vergleich aktueller vs. vorheriger Zeitraum
- Top-Spieler-Leaderboard mit Medaillen
- Savegame-Wachstums-Tracking (Start/Ende mit % Aenderung)
- 1-Stunden und Langzeit-Performance-Durchschnitte

### 4.5 giveaway_cog.py (984 Zeilen)

**Slash Commands:** `/giveaway create|end|reroll|cancel|list`

**Teilnahme-Anforderungen:**
- Minimum-Level (via bot.level_manager)
- Benoetigte Rolle
- Mindest-Mitgliedschaftsdauer
- Gastgeber kann nicht teilnehmen

**Implementierungsdetails:**
- Persistenter "Teilnehmen"-Button mit `custom_id`
- Auto-End via 30-Sekunden Background Task
- Gewinner-Benachrichtigung: Channel + DM
- Bis zu 20 Gewinner pro Giveaway
- Max. 30 Tage Laufzeit
- SQLite-Persistenz (Ladevorgang beim Cog-Load)

### 4.6 leveling_cog.py

**Slash Commands:** `/level`, `/leaderboard`, `/xp`, `/level-config`

**XP-System:**
- Nachrichten-XP (konfigurierbar, Cooldown)
- Voice-Aktivitaets-XP (minutenbasiert)
- Level-Up-Benachrichtigungen
- Rollenbelohnungen bei bestimmten Leveln
- Admin-XP-Vergabe/-Entzug

### 4.7 tickets_cog.py

**Slash Commands:** `/ticket`

**System:**
- Modal-basierte Erstellung (Betreff + Beschreibung)
- Eigener privater Channel pro Ticket
- Ticket-Transkript vor Schliessung
- Kategorisierung und Zuweisungen
- Schliess-Berechtigung: Ersteller + Admins

### 4.8 warn_cog.py

**Slash Commands:** `/warn add|remove|list|clear|config`

**Eskalation:**
- Punkte-basierte Schwellwerte
- Auto-Aktionen: Mute, Kick, Ban
- Verwarnungshistorie pro Benutzer
- Admin-konfigurierbare Schwellwerte

### 4.9 server_backup_cog.py (1072 Zeilen)

**Slash Commands:** `/server backup create|list|info|compare|restore|delete|auto`

**Restore-Modi:**
- `full`: Destruktiv, loescht nicht-passende Elemente
- `roles_only`: Nur Rollen erstellen/aktualisieren
- `channels_only`: Nur Channels/Kategorien
- `add_missing`: Sicher, fuegt nur Fehlende hinzu

**Gesichert werden:**
- Channels, Rollen, Kategorien, Emojis
- Channel-Hierarchie, Berechtigungen, Einstellungen
- Guild-Settings (Verifikation, AFK-Channel, etc.)

### 4.10 update_cog.py (980 Zeilen)

**MC Modpack-Befehle:** `/mc modpack status|update|force-update|cancel|rollback|history|check`
**SAT Update-Befehle:** `/sat update start|cancel`

**MC Modpack:**
- CurseForge-Integration (Check + Auto-Update)
- 10-Minuten Countdown (konfigurierbar)
- Rollback zu vorheriger Version
- NeoForge-Versions-Tracking
- Versionshistorie mit Status-Icons

**SAT Update:**
- SteamCMD-Integration
- In-Game-Warnungen vor Shutdown
- Build-ID-Tracking
- HAR-Unterdrueckung waehrend Updates

### 4.11 Weitere Cogs (Kurzuebersicht)

| Cog | Beschreibung |
|-----|-------------|
| `audit_cog.py` | Loggt Discord-Events (Join/Leave/Edit/Delete) in Admin-Channel |
| `command_stats_cog.py` | Trackt Slash-Command-Nutzung pro User/Command |
| `custom_commands_cog.py` | Benutzerdefinierte Text-Commands mit Variablen-Substitution (`{user}`, `{server}`) |
| `embed_sender_cog.py` | Verarbeitet Embed-Queue aus dem Web-Dashboard |
| `general_cog.py` | `/help` (paginiert), `/clear` (Nachrichten loeschen), `/reload` (Cog-Reload), `/ping` |
| `maintenance_mode_cog.py` | Globaler Wartungsmodus mit Auto-Timeout-Safety |
| `mod_cog.py` | Read-only Mod-Info fuer SAT + MC |
| `moderation_cog.py` | Wortfilter (on_message) + Anti-Spam-Erkennung |
| `notify_cog.py` | Opt-in Benachrichtigungen fuer Serverstatus-Aenderungen |
| `profile_cog.py` | Spieler-Statistik-Karten + Leaderboard |
| `reaction_roles_cog.py` | Emoji -> Rolle Setup via Admin-Command |
| `shutdown_cog.py` | Geplanter Shutdown mit In-Game-Countdown-Warnungen |
| `teamspeak_cog.py` | TS-Kanalverwaltung + Discord<->TS Chat-Bridge |
| `temp_voice_cog.py` | Join-to-Create temporaere Voice-Channels |
| `welcome_cog.py` | Willkommensnachrichten + Auto-Rollenzuweisung |
| `timeout_cog.py` | Multi-Server Temp-Bans (SAT + MC mit IP-Bans) |

---

## 5. Module — Kernlogik

### 5.1 Minecraft-Subsystem (`modules/minecraft/`)

#### server.py — MinecraftServer
- Systemd-Integration: `start()`, `stop()`, `restart()`, `is_running()`
- RCON-Ausfuehrung: `execute_rcon(command)`
- Spielerzaehlung via `list`-Command-Parsing
- Uptime aus systemd
- Multi-Server via ENV-Prefix: `MC_{SERVER_ID}_*`

#### rcon.py — MinecraftRCON
- Async Socket-Kommunikation mit Auto-Reconnect
- RCON-Protokoll: `length(4) + request_id(4) + type(4) + payload + 2 nulls`
- Login-Authentifizierung (response_id == -1 = Fehler)
- Multi-Packet-Response-Handling (max 64 Pakete)
- Lock-basierte Nebenlaeufikeitskontrolle

#### chat_bridge.py — MinecraftChatBridge
- Log-Polling: Liest MC-Server-Log in Echtzeit
- Erkennt: Player-Join/Leave, Chat-Nachrichten, Advancements, Deaths
- Discord -> MC: Sendet via RCON `/say [DC] Username: Nachricht`
- Max. Nachrichtenlaenge: 200 Zeichen (konfigurierbar)
- Prefix: `[DC]`

#### backup.py — MinecraftBackupManager
- Async Backup-Erstellung mit Fehlertoleranz
- Metadaten-Tracking (created_by, timestamp)
- Auto-Rotation (max_backups Limit)
- Restore mit Safety-Backup vor Ueberschreiben
- Pfad-Traversal-Schutz

#### blacklist.py — MinecraftBlacklist
- Cross-Server Ban-System
- SQLite-Backend (blacklist + bans Tabellen)
- RCON-Synchronisation zu allen aktiven Servern
- Ban-Historie pro Spieler
- Async Lock fuer Thread-Sicherheit

#### update_manager.py — UpdateManager (8 Phasen)
1. Crash-Recovery beim Bot-Start
2. 10-Minuten Countdown (MCCountdownTimer)
3. HAR-Unterdrueckung
4. Disk-Space-Check
5. Download + Hash-Verifikation (SHA1/MD5)
6. Atomic Swap mit Rollback-Faehigkeit
7. 3 Startversuche mit Validierung
8. RCON Health-Check nach Start

#### modpack_updater.py — CurseForge-Integration
- Rate-Limited API-Requests (2s Minimum)
- Exponential Backoff fuer 429-Responses
- Version-Extraktion via Regex
- Server-Pack-Details (Download-URL, Hashes, Groesse)
- SQLite-Persistenz fuer aktuelle Version

#### neoforge_updater.py — NeoForge-Versionsmanagement
- Multi-Methoden Versionserkennung (variables.txt, JAR-Name, run.sh)
- ServerPackCreator-Pack-Support
- EULA-Check-Patching fuer systemd
- variables.txt Patching (WAIT_FOR_USER_INPUT, RESTART)

#### file_manager.py — FileManager
- Disk-Space Pre-Flight-Checks (min. 1.2 GB)
- Streaming-Download mit Progress-Callbacks (8 KB Chunks)
- SHA1/MD5 Hash-Verifikation mit Retries (max 3)
- ZIP-Extraktion mit Integritaets-Validierung
- Atomic Swap (staging -> production) via sudo
- Rollback-Rotation (haelt N Versionen)
- Timeout: 60 Minuten fuer grosse Dateien

#### world_analyzer.py — Welt-Analyse
- nbtlib fuer level.dat Parsing
- Spieler-Statistik-Aggregation (Spielzeit, Tode, Top-Bloecke)
- Advancement-Tracking (Fortschritt in %)
- Region-File-Analyse (erkundete km2)
- Parallele async Analyse-Tasks

### 5.2 Satisfactory-Subsystem (`modules/satisfactory/`)

#### api_client.py — SatisfactoryAPI
- HTTPS-Client mit Self-Signed-Certificate-Handling
- Retry-Logik (3 Versuche)
- Session-Management mit Locking
- POST-basierte JSON-API mit Bearer-Token
- Endpunkte: `QueryServerState`, `RunCommand`, `SaveGame`, etc.

#### server.py — SatisfactoryServer
- Systemd-Integration (start/stop/restart)
- Prozess-Discovery via psutil (`FactoryServer-Linux-Shipping`)
- `/proc`-Fallback bei AccessDenied
- CPU, Memory, Uptime Tracking

#### savegame_analyzer.py — Tiefe Savegame-Analyse
- Verwendet `satisfactory-save` Paket (optional)
- Gebaeude-Zaehlung nach Typ (Baender, Generatoren, Produktion)
- Tier-Tracking (MK1-MK6 Baender)
- Stromerzeugungsanalyse
- Transportsystem-Tracking (Zuege, Drohnen, Fahrzeuge)

#### save_header.py — Binaerer Header-Parser
- struct-basiertes Parsing (Little-Endian)
- Windows FILETIME-Konvertierung
- Header-Version, Save-Version, Build-Version
- Session-Name, Map-Name, Spielzeit, Speicherdatum

#### blueprint_manager.py — Blueprint-Verwaltung
- Auto-Erkennung des aktiven Welt-Ordners
- Blueprint-Synchronisation bei Weltwechsel
- Kategorie-System (Produktion, Logistik, etc.)
- Upload-Validierung

### 5.3 Monitoring-Subsystem (`modules/monitoring/`)

#### health_checker.py — HealthAutoRestart
- Failure-Schwelle: 3 aufeinanderfolgende Fehler -> Restart
- Restart-Cooldown: 30 Minuten pro Server
- UDP Lightweight Query + TCP Fallback (SAT)
- `suppress()`/`unsuppress()` fuer geplante Wartung
- Farbkodierte Discord-Embeds (Warnung/Restart/Recovery)

#### performance.py — PerformanceMonitor
- 288-Eintrag Ring-Buffer (24h bei 5min Intervallen)
- Warnungs-Cooldown (300s) gegen Spam
- Prozess-spezifische Metriken wenn Server-Objekt vorhanden

#### stats_collector.py — Metriken-Sammlung
- 5-Minuten Standard-Intervall
- In-Memory Ring-Buffer (max 8640 Eintraege = 30 Tage)
- Liest Server-Status aus `data/monitor/*_status.json`
- Schreibt in `stats_history` Tabelle

#### service_watchdog.py — systemd-Ueberwachung
- Check-Intervall: 2 Minuten
- Max 3 Restarts pro Stunde pro Service
- Restart-Verifikation: wartet 3s, prueft ob "active"
- Callbacks: on_service_down, on_restart_success, etc.

#### crash_replay.py — Log-Kontext-Sicherung
- Deque Ring-Buffer (50 Zeilen Standard)
- Erkennt Log-Rotation (Dateigroesse-Tracking)
- Speichert Replay-Dateien mit Header
- Max 20 Replay-Dateien

#### forecasting.py — Ressourcen-Prognose
- Pure Python Least-Squares lineare Regression
- Slope-Einheiten: % pro Stunde -> % pro Tag
- Warnung: <30 Tage bis voll oder <20% frei
- Kritisch: <7 Tage oder <10% frei
- Minimum 10 Datenpunkte fuer Regression

#### auto_cleanup.py — Automatische Bereinigung
- Retention: Logs 30d, Crash-Replays 20d, Backups 30d
- Komprimierung nach 3 Tagen

#### graceful_degradation.py — Reduzierter Betrieb
- Weiterbetrieb bei Teilausfaellen
- Feature-Deaktivierung statt Komplett-Absturz

#### savegame_protection.py — Integritaetsschutz
- Crash-Erkennungsfenster: 10 Minuten
- Max 3 Crashes erlaubt
- Groessen-Drop-Schwelle: 50% (Korruptionsindikator)

### 5.4 Backup-Subsystem (`modules/backup/`)

#### backup_manager.py — Server-Backup
- tar.gz Kompression mit optionaler Verifikation
- SQLite `backup_history` als autoritaeve Quelle
- Auto-Rotation (max 20 Backups)
- Restore erstellt zuerst Sicherheits-Backup

#### onedrive_backup.py — Cloud-Backup via rclone
- `rclone copyto` (atomarer Copy)
- Upload-Timeout: 600s (10 Minuten)
- Rotation: Max 10 Cloud-Backups

#### config_backup.py — Config -> OneDrive
- Gesichert: .env, config.json, data/, rclone.conf, sudoers, systemd
- Optionale GPG-Verschluesselung (AES256)
- Max 10 Config-Backups in Cloud

#### integrity.py — Backup-Verifizierung
- SHA256-Hash (64 KB Chunks)
- tar-Test mit 120s Timeout
- Groessen-Plausibilitaet: <50% oder >200% = Fehler

### 5.5 Datenbank-Module (`modules/database/`)

Siehe [Abschnitt 8: Datenbank](#8-datenbank-sqlite) fuer Details.

### 5.6 Analytics (`modules/analytics/`)

#### correlation.py — CorrelationAnalyzer
- Pearson-Korrelation: Crashes vs. Spielerzahl
- RAM vs. Spielzeit (Memory-Leak-Erkennung)
- Crash-Muster: 24h-Buckets + 7-Tage-Buckets
- Anomalie-Erkennung: >2 Standardabweichungen
- 30-Tage Analysefenster

### 5.7 Weitere Module

| Modul | Beschreibung |
|-------|-------------|
| `alert_dedup.py` | Alert-Deduplizierung mit Cooldown-Intervallen |
| `audit_logger.py` | Audit-Event-Logger fuer Discord-Events |
| `command_logger.py` | Befehls-Audit-Trail in Admin-Channel |
| `config_history.py` | Konfigurations-Aenderungsverlauf |
| `config_reloader.py` | Hot-Reload fuer config.json Aenderungen |
| `config_validator.py` | Schema-Validierung der Konfiguration |
| `giveaways.py` | Gewinnspiel-Manager (SQLite CRUD) |
| `leveling.py` | XP/Level-Berechnung + Rollenbelohnungen |
| `maintenance.py` | Bot-Wartungsstatus-Verwaltung |
| `mod_manager.py` | Mod-Installation und -Verwaltung |
| `restart_timer.py` | Countdown-Timer mit Discord-Nachrichten |
| `server_backup.py` | Discord-Server-Struktur-Backup/Restore |
| `temp_voice.py` | Join-to-Create Voice-Channel-Logik |
| `temp_voice_views.py` | Discord UI Views fuer Temp-Voice |
| `tickets.py` | Support-Ticket CRUD-Operationen |
| `timeout_manager.py` | Timeout-Verwaltung mit Ablauf |
| `warn_manager.py` | Verwarnungs-Punkte + Eskalation |
| `word_filter.py` | Regex-basierter Wortfilter |

### 5.8 Netzwerk, Security, System

| Modul | Beschreibung |
|-------|-------------|
| `network/duckdns_monitor.py` | DNS vs. tatsaechliche IP (api.ipify.org), taeglich |
| `network/port_monitor.py` | Port-Erreichbarkeits-Check |
| `security/ban_manager.py` | IP-Ban via iptables (INPUT/OUTPUT REJECT) |
| `security/fail2ban.py` | Fail2Ban-Integration/Monitoring |
| `security/ssl_monitor.py` | SSL-Zertifikats-Ablauf-Tracking |
| `system/disk_guard.py` | 3-Stufen: 20% Warn, 10% Cleanup, 5% Kritisch |
| `system/package_checker.py` | apt-Paket-Aktualisierungen |

### 5.9 Notifications

#### discord_notifier.py — DiscordNotifier
- `NotifyLevel` Enum: INFO, SUCCESS, WARNING, ERROR, CRITICAL
- Farbkodierte Embeds pro Severity
- Channel-basiertes Routing
- Rollen-Ping Support
- Crash/Player/Service-Benachrichtigungen

#### email_notifier.py — EmailNotifier
- SMTP mit TLS
- Rate-Limiting: 1 E-Mail pro Typ pro 15 Minuten
- HTML E-Mail-Formatierung
- Event-Typ-basierte Cooldowns

### 5.10 TeamSpeak-Integration

| Modul | Beschreibung |
|-------|-------------|
| `teamspeak/ts_client.py` | TS3 ServerQuery Client (async, Auto-Reconnect) |
| `teamspeak/channel_manager.py` | TS-Kanal-Verwaltung |
| `teamspeak/chat_bridge.py` | Discord <-> TeamSpeak Chat-Bruecke |

---

## 6. Web-Dashboard (FastAPI)

### 6.1 App-Aufbau (`web/app.py`)

**Middleware-Stack (Reihenfolge von aussen nach innen):**
1. SessionMiddleware (24h max_age, httponly, lax SameSite)
2. SessionTimeoutMiddleware — Prueft Session-Ablauf
3. CSRFMiddleware — Token-basierter CSRF-Schutz
4. RateLimitMiddleware — IP-basiert
5. CORSMiddleware — Domain/IP Allowlist

**Features:**
- WebSocket-Endpoint `/ws` fuer Echtzeit-Updates
- Static Files unter `/static` (htmx.min.js, style.css, themes.css)
- Jinja2 Templates aus `web/templates/`
- Startup: DB-Init, Shutdown: DB-Close

### 6.2 Authentifizierung (`web/auth.py`)

**Dual-Auth-System:**
1. **Primaer:** Discord OAuth2 (Guild-Membership + Rollen-Check)
2. **Fallback:** Benutzername/Passwort (bcrypt)

**Ablauf:**
- `/auth/login` — Login-Seite mit OAuth-Link
- `/auth/discord` — Redirect zu Discord-Autorisierung
- `/auth/discord/callback` — OAuth-Callback mit Guild/Rollen-Check
- `/auth/login` (POST) — Fallback-Login
- `/auth/logout` — Session loeschen + JWT-Cookie entfernen

**Autorisierung:**
- JWT-Token (HS256, 24h Ablauf)
- Rate-Limiting: 5 Login-Versuche pro 900s pro IP
- OAuth State-Token-Validierung (CSRF-Schutz)
- `WEB_ALLOWED_USER_IDS` fuer direkte Freigabe

### 6.3 Middleware

#### csrf.py
- Token in Session generiert, bei POST/PUT/DELETE validiert
- Ausnahmen: `/auth/*`, `/api/*`, `/webhook/*`

#### rate_limiter.py
- IP-basiertes Sliding-Window
- Konfigurierbare Limits pro Pfad-Prefix
- 429 Too Many Requests Response

#### session_timeout.py
- Session-Ablauf nach Inaktivitaet
- `last_activity` Timestamp in Session

### 6.4 Routes (19 Module)

| Route | Pfad | Beschreibung |
|-------|------|-------------|
| `dashboard.py` | `/` | Hauptuebersicht: Server/Bot-Status, Performance, Events |
| `server_detail.py` | `/server/<name>` | Server-Detail mit Spieler, Backups, Mods |
| `admin_bot_route.py` | `/admin-bot` | Admin-Bot Konfiguration (Tabs: AntiSpam, Audit, Embeds, etc.) |
| `analytics_route.py` | `/analytics` | Heatmaps + Analytics-Dashboard |
| `backup_status_route.py` | `/backup-status` | Backup-Status aller Server |
| `changelog_route.py` | `/changelog` | CHANGELOG.md Rendering |
| `config_route.py` | `/config` | Konfigurations-Editor |
| `config_reload_route.py` | `/config/reload` | Hot-Reload Trigger |
| `correlation_route.py` | `/correlation` | Korrelations-Dashboard |
| `errors_route.py` | `/errors` | Error-Uebersicht |
| `export_route.py` | `/export` | CSV/JSON Daten-Export |
| `forecast_route.py` | `/forecast` | Ressourcen-Prognose (Disk/RAM) |
| `health_route.py` | `/api/health` | Health-Check-API + Selftest |
| `search_route.py` | `/search` | FTS5 Volltextsuche |
| `security_route.py` | `/security` | Security-Dashboard (Fail2Ban, SSL, Bans) |
| `sse_route.py` | `/sse` | Server-Sent Events Stream |
| `system_route.py` | `/system` | System-Uebersicht (CPU, RAM, Disk, Services) |
| `theme_route.py` | `/theme` | Dark-Mode-Toggle |
| `webhook_route.py` | `/webhook` | Webhook-Integration |

### 6.5 Templates

**Basis-Template:** `base.html` — Navigation, Footer, CSS/JS
**Partials (HTMX):**
- `admin_tab_antispam.html`, `admin_tab_audit.html`, `admin_tab_embeds.html`
- `admin_tab_giveaways.html`, `admin_tab_leveling.html`, `admin_tab_reaction_roles.html`
- `admin_tab_teamspeak.html`, `admin_tab_temp_voice.html`, `admin_tab_tickets.html`
- `admin_tab_warn.html`, `admin_tab_wordfilter.html`
- `analytics.html`, `config_bot_profiles.html`, `config_login.html`
- `config_notifications.html`, `server_backups.html`, `server_mods.html`, `server_players.html`

---

## 7. Utilities

### 7.1 config.py — Konfigurationsloader

**Funktionen:**
- `load_env()` — Laedt `.env` aus `config/` (Fallback: Projekt-Root)
- `get_env(key, default, cast)` — ENV-Variable mit optionalem Type-Cast (int, float, bool)
- `get_config()` — Laedt `config.json` mit Feature-Toggles
- `save_config(config)` — Schreibt config.json (Dashboard-Nutzung)

**Konstanten:**
- `PROJECT_ROOT` — Zwei Ebenen ueber `utils/`
- `CONFIG_DIR`, `DATA_DIR`, `LOG_DIR`
- `GAMESERVER_DATA_DIR`, `MONITOR_DATA_DIR`, `ADMIN_DATA_DIR`

**Bool-Casting:**
- `"true"`, `"1"`, `"yes"`, `"on"` (case-insensitive) -> `True`

### 7.2 logger.py — Logging-Setup

**Factory:** `get_logger(name, log_file, level)`

- Format: `"%(asctime)s [%(name)s] %(levelname)s: %(message)s"` (Deutsch: `%d.%m.%Y %H:%M:%S`)
- Console: stdout
- File: `RotatingFileHandler` (10 MB, 5 Backups, UTF-8)
- Logger-Cache: `_loggers` Dict verhindert doppelte Handler
- Dateinamen: `.` und `/` werden zu `_` konvertiert

### 7.3 permissions.py — Zugriffskontrolle

**Hierarchie:** Owner > Admin > Player > Everyone

**Check-Funktionen:**
- `is_owner(interaction)` — OWNER_ID Match
- `is_admin(interaction)` — Owner ODER ADMIN_ROLE_ID
- `is_spieler(interaction)` — Admin ODER SATISFACTORY_ROLE_ID

**Decorators:**
- `@owner_only()` — Nur Bot-Owner
- `@admin_only()` — Admins + Owner
- `@spieler_only()` — Spieler + Admins + Owner
- `@server_online_required(server_attr)` — Prueft ob Server laeuft (async `is_running()`)

### 7.4 formatting.py — Format-Helfer

| Funktion | Beispiel |
|----------|---------|
| `format_uptime(3690)` | `"1h 1m"` |
| `format_bytes(1536000000)` | `"1.43 GB"` |
| `format_timestamp()` | `"07.04.2026 14:30:00"` |
| `format_duration_minutes(150)` | `"2h 30m"` |
| `truncate(text, 100)` | Text mit `...` bei Ueberschreitung |
| `status_emoji(True)` | `"🟢"` |
| `progress_bar(4, 10)` | `"[####------] 40%"` |

### 7.5 selftest.py — Pre-Boot-Verifikation

**Einstiegspunkte:**
- `execute_selftest(bot_name, required_env, required_paths)` -> bool
- `run_selftest(...)` -> list[SelftestResult]
- `get_selftest_json(...)` -> dict (fuer `/api/health/selftest`)

**8 Pruefkategorien:**
1. Config: config.json existiert, valides JSON, erforderliche Keys
2. ENV: Alle Required-Variablen gesetzt
3. Pfade: CONFIG_DIR, DATA_DIR, LOG_DIR existieren + schreibbar
4. Dependencies: 14 Pakete geprueft (kritisch bei >3 fehlend)
5. DNS: discord.com aufloesung
6. Daten-Verzeichnisse: gameserver/, monitor/, admin/

### 7.6 shutdown.py — Graceful Shutdown

**Shutdown-Sequenz:**
1. Admin-Channel benachrichtigen (falls konfiguriert)
2. Cleanup-Callbacks in LIFO-Reihenfolge (5s Timeout pro Callback)
3. Bot-Verbindung schliessen
4. Log-Abschluss

**Signal-Handling:**
- Erster SIGTERM/SIGINT: Setzt Flag, startet Shutdown
- Zweiter Signal: Sofortiger Exit
- Windows-Fallback: `signal.signal()` wenn `add_signal_handler()` fehlt
- Timeout: 15 Sekunden fuer erzwungenen Exit

---

## 8. Datenbank (SQLite)

### 8.1 Verbindungsmanager (`db_manager.py`)

- Einzelne globale Connection pro Prozess
- asyncio Lock fuer Thread-Sicherheit
- WAL-Modus: Gleichzeitiges Lesen und Schreiben
- PRAGMA: synchronous=NORMAL, busy_timeout=5000, foreign_keys=ON
- `DBHelper`-Wrapper: `fetch_one()`, `execute()`, etc.
- Integritaetspruefung: `check_integrity()`, `get_table_counts()`

### 8.2 Schema (migrations.py) — 4 Versionen

**V1 (23 Basistabellen):**
- `players` — Spielerdaten
- `player_sessions` — Sitzungsverlauf
- `player_ips` — IP-Adressen-Tracking
- `stats_history` — System-Metriken-Zeitreihe
- `events` — Spiel-/System-Events
- `warns` — Verwarnungen
- `tickets` — Support-Tickets
- `ticket_messages` — Ticket-Nachrichten
- `leveling_users` — XP/Level-Daten
- `giveaways` — Gewinnspiele
- `giveaway_participants` — Teilnehmer
- `bans` — Bans/Timeouts
- `backup_history` — Backup-Metadaten
- `audit_log` — Audit-Eintraege
- `command_log` — Befehls-Protokoll
- `whitelist` — Whitelist-Eintraege
- `blacklist` — Blacklist-Eintraege
- `scheduled_tasks` — Geplante Aufgaben
- `custom_commands` — Benutzerdefinierte Befehle
- `notify_subscriptions` — Benachrichtigungs-Abos
- `config_changes` — Konfigurations-Historie
- `alerts_sent` — Gesendete Alerts
- `search_index` — FTS5 Volltextsuche

**V2:** `server_stats_tracker`
**V3:** `reaction_roles`
**V4:** `modpack_updates`, `server_versions` (Auto-Update-System)

### 8.3 Datenmodelle (`models.py`) — 20 Dataclasses

Jede Klasse hat `from_row()` Classmethod fuer Row -> Dataclass Konvertierung.

### 8.4 Wartung (`maintenance.py`)

**Backup-Rotation:**
- 24 stuendliche + 7 taegliche + 4 woechentliche Backups
- SQLite Backup API in separatem Thread

**Retention:**
| Tabelle | Aufbewahrung |
|---------|-------------|
| stats_history | 90 Tage |
| events | 30 Tage |
| audit_log | 365 Tage |
| command_log | 90 Tage |
| alerts_sent (resolved) | 30 Tage |
| modpack_updates (failed) | 90 Tage |
| scheduled_tasks | 7 Tage |

### 8.5 Volltextsuche (`search_indexer.py`)

- FTS5 Virtual Table
- 6 indexierte Quellen: events, players, audit_log, command_log, backup_history, custom_commands
- Inkrementelles Update seit letzter ID
- Snippet-Generierung mit `<mark>`-Tags

### 8.6 JSON-Import (`json_importer.py`)

- Einmaliger Import historischer JSON-Daten in SQLite
- Idempotent: Prueft bestehende Daten vor Import
- 13 Sub-Importer (Spieler, IPs, Events, Warns, Tickets, Leveling, etc.)
- Original-JSON-Dateien werden beibehalten

---

## 9. Konfiguration und Environment-Variablen

### 9.1 config.json — Feature-Toggles

```json
{
  "features": {
    "chat_bridge": true,
    "word_filter": true,
    "anti_spam": true,
    "player_tracking": true,
    "auto_backup": true,
    "onedrive_backup": true,
    "email_notifications": false,
    "auto_update": true,
    "daily_restart": true,
    "steam_changelog": true,
    "voice_stats": true,
    "status_embed": true,
    "login_audit": true,
    "auto_cleanup": true,
    "service_watchdog": true,
    "disk_guard": true,
    "duckdns_monitor": true,
    "port_monitor": true,
    "ssl_monitor": true,
    "savegame_protection": true,
    "backup_integrity": true,
    "graceful_degradation": true,
    "sqlite_migration_complete": false
  }
}
```

**Schwellwerte:**
- CPU: 80%, RAM: 85%, Disk: 90%/95%, Tick-Rate: 20, Cooldown: 300s

**Scheduler-Zeiten:**
- Daily Restart: 04:00, Auto-Backup: 6h, Update-Check: 6h
- Config-Backup: 03:00, Auto-Cleanup: 02:00
- Health Auto-Restart: max 3 Failures, 30min Cooldown
- Service Watchdog: 120s, Disk Guard: 600s

**Anti-Spam:**
- Max 5 Nachrichten/10s, Command-Limit: 3/10s, Cooldown: 30s

**Restart-Timer:**
- Default: 10min, Warnungen bei [10, 5, 3, 1] Minuten

### 9.2 .env — Environment-Variablen (13 Sektionen)

#### Bot-Tokens (ERFORDERLICH)
```
DISCORD_TOKEN_MANAGER=    # GameServer Bot Token
DISCORD_TOKEN_WATCHDOG=   # Monitor Bot Token
ADMIN_BOT_TOKEN=          # Admin Bot Token
```

#### Discord IDs (ERFORDERLICH)
```
GUILD_ID=                 # Discord Server ID
OWNER_ID=                 # Bot-Owner User ID
ADMIN_ROLE_ID=            # Admin-Rollen ID
SATISFACTORY_ROLE_ID=     # Spieler-Rollen ID
MINECRAFT_ROLE_ID=        # MC-Spieler-Rollen ID
```

#### Channels
```
ADMIN_LOG_CHANNEL_ID=     # Admin-Log-Channel
PUBLIC_STATUS_CHANNEL_ID= # Oeffentlicher Status
STATUS_EMBED_CHANNEL_ID=  # Status-Embed Channel
```

#### Satisfactory
```
API_TOKEN=                # HTTPS API Token
SATISFACTORY_USER=        # System-User
SATISFACTORY_SERVICE=     # systemd Service Name
SATISFACTORY_SERVER_PATH= # Server-Installationspfad
```

#### Minecraft (Multi-Server, pro Server-Block)
```
MC_BMC_SERVICE=           # systemd Service (BMC)
MC_BMC_PATH=              # Server-Pfad
MC_BMC_RCON_PORT=         # RCON Port
MC_BMC_RCON_PASSWORD=     # RCON Passwort
MC_BMC_CHAT_CHANNEL_ID=   # Chat-Bridge Channel (0=deaktiviert)

MC_VANILLA_SERVICE=       # Analog fuer Vanilla
MC_VANILLA_PATH=
MC_VANILLA_RCON_PORT=
MC_VANILLA_RCON_PASSWORD=
MC_VANILLA_CHAT_CHANNEL_ID=
```

#### Backup + Cloud
```
BACKUP_PATH=              # Lokales Backup-Verzeichnis
ONEDRIVE_ENABLED=         # true/false
ONEDRIVE_REMOTE=          # rclone Remote-Name
ONEDRIVE_PATH=            # Cloud-Pfad
```

#### E-Mail (optional)
```
EMAIL_ENABLED=            # true/false
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASS=
EMAIL_FROM=
EMAIL_TO=
```

#### Auto-Update (v4.1)
```
CURSEFORGE_API_KEY=               # CurseForge API Key
MC_BMC_CURSEFORGE_PROJECT_ID=     # Modpack Projekt-ID
MC_BMC_CURSEFORGE_FILE_ID=        # Aktuelle Datei-ID
MC_BMC_PRESERVE_FILES=            # Beizubehaltende Dateien
MC_VANILLA_PRESERVE_FILES=
UPDATE_STAGING_PATH=              # Download-Verzeichnis
```

#### Web-Dashboard
```
WEB_ENABLED=true
WEB_PORT=8080
WEB_HTTPS=true
WEB_DOMAIN=               # Oeffentliche Domain
WEB_SECRET_KEY=            # JWT Secret (min 32 Zeichen)
DISCORD_CLIENT_ID=         # OAuth2 Client ID
DISCORD_CLIENT_SECRET=     # OAuth2 Client Secret
WEB_ADMIN_USER=            # Fallback Username
WEB_ADMIN_PASS_HASH=       # bcrypt Hash
```

#### TeamSpeak (optional)
```
TS_ENABLED=false
TS_HOST=
TS_PORT=
TS_USER=
TS_PASSWORD=
```

#### Sonstiges
```
SERVER_IP=                # Oeffentliche Server-IP
GPG_PASSPHRASE=           # Config-Backup Verschluesselung
```

---

## 10. Scripts und Deployment

### 10.1 deploy.sh — Haupt-Deployment

Kopiert Code von Entwicklungs-Rechner auf Server, erstellt .bak Backups, aktualisiert pip-Pakete, restartet systemd Services.

### 10.2 auto_deploy.sh — Automatisches Deployment

Git-Pull + Service-Restart Automation.

### 10.3 manage_bots.sh — Bot-Verwaltung

```bash
bash scripts/manage_bots.sh status    # Status aller Bots
bash scripts/manage_bots.sh start     # Alle Bots starten
bash scripts/manage_bots.sh stop      # Alle Bots stoppen
bash scripts/manage_bots.sh restart   # Alle Bots neustarten
```

### 10.4 bot_watchdog.sh — Watchdog

Prueft ob Bot-Prozesse laufen, startet bei Ausfall neu.

### 10.5 setup_nginx.sh — Nginx + SSL

Let's Encrypt Zertifikat-Setup, Reverse-Proxy Konfiguration.

### 10.6 setup_minecraft.sh — MC-Server Setup

Minecraft Server Installation und Konfiguration.

### 10.7 optimize_server.sh — Server-Optimierung

System-Tuning fuer Game-Server-Betrieb.

### 10.8 rcon_op.py — RCON OP-Script

Standalone-Script fuer RCON-Befehle (ausserhalb des Bot-Kontexts).

---

## 11. Systemd-Services

### admin-bot.service
```ini
[Unit]
Description=Discord Admin Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/home/botuser/Discord_Bots
ExecStart=/usr/bin/python3 bots/admin_bot.py
Restart=on-failure
RestartSec=10
```

### gameserver-bot.service
```ini
[Service]
ExecStart=/usr/bin/python3 bots/gameserver_bot.py
# Gleiche Struktur wie admin-bot
```

### monitor-bot.service
```ini
[Service]
ExecStart=/usr/bin/python3 bots/monitor_bot.py
# Gleiche Struktur
```

### web-dashboard.service
```ini
[Service]
ExecStart=/usr/bin/python3 -m uvicorn web.app:app --host 127.0.0.1 --port 8080
# Gebunden an localhost, Nginx als Reverse-Proxy
```

### bot-watchdog.service + bot-watchdog.timer
- Timer-Unit: Fuehrt bot_watchdog.sh periodisch aus
- Prueft ob alle Bot-Services laufen
- Automatischer Restart bei Ausfall

---

## 12. Tests

### test_imports.py
Prueft ob alle Module ohne ImportError importiert werden koennen. Deckt alle Cogs, Module, Utils und Web-Routen ab.

### test_cogs.py
Validiert dass alle Cogs die `setup()`-Funktion haben und korrekt strukturiert sind.

### test_env_completeness.py
Vergleicht `.env.example` mit den tatsaechlich im Code verwendeten ENV-Variablen. Stellt sicher, dass keine Variable fehlt.

### test_routes.py
Prueft ob alle Web-Routes korrekt registriert werden und importierbar sind.

### test_server_bots.py
Server-Integrationstests fuer die drei Bots.

### test_server_dashboard.py
Dashboard-Integrationstests.

### test_sat_status.py / test_sat_debug.py
Satisfactory-spezifische Tests.

### test_server_update.py
Update-System-Tests.

**Test-Ausfuehrung (alle 4 muessen bestehen):**
```bash
python tests/test_imports.py
python tests/test_routes.py
python tests/test_cogs.py
python tests/test_env_completeness.py
```

---

## 13. Abhaengigkeiten

### requirements.txt

**Kern:**
- `discord.py>=2.3.0` — Discord Bot Framework
- `python-dotenv>=1.0.0` — ENV-Laden
- `aiohttp>=3.9.0` — Async HTTP Client
- `aiofiles>=23.0.0` — Async Dateioperationen
- `psutil>=5.9.0` — System-Monitoring
- `aiosqlite>=0.19.0` — Async SQLite

**Web-Dashboard:**
- `fastapi>=0.110.0` — Async Web Framework
- `uvicorn>=0.27.0` — ASGI Server
- `httpx>=0.27.0` — Async HTTP Client
- `PyJWT>=2.8.0` — JWT Tokens
- `bcrypt>=4.1.0` — Passwort-Hashing
- `jinja2>=3.1.0` — Template Engine
- `python-multipart>=0.0.7` — Form Parsing
- `itsdangerous>=2.1.0` — Secure Signing

**Optionale Features:**
- `satisfactory-save>=0.1.0` — Tiefe Savegame-Analyse
- `nbtlib>=2.0.0` — MC NBT Parsing (level.dat, Stats)
- `ts3>=2.0.0` — TeamSpeak ServerQuery

**Extern (nicht in requirements.txt):**
- `rclone` — OneDrive-Backup
- `SteamCMD` — SAT-Server-Updates
- `nginx` — Reverse Proxy
- `certbot` — Let's Encrypt
- `gpg` — Config-Verschluesselung
- `iptables`/`ufw` — IP-Bans

---

## 14. Patterns, Besonderheiten und Edge Cases

### 14.1 Architektur-Patterns

**Cog-basiertes Hot-Reloading:**
Alle Discord-Befehle sind in Cogs organisiert, die zur Laufzeit geladen/entladen werden koennen. `/reload` laedt alle Cogs neu ohne Bot-Neustart.

**Feature-Toggle-System:**
Jedes Feature kann in `config.json` aktiviert/deaktiviert werden. Cogs und Module pruefen den Toggle vor der Ausfuehrung. Ermoeglicht schrittweises Aktivieren neuer Features.

**Multi-Server-Architektur:**
Minecraft-Server werden dynamisch basierend auf `MC_*_SERVICE` ENV-Variablen erkannt. Jeder Server bekommt eigene Manager-Instanzen (Backup, Mods, Tracker). Dict-basierter Zugriff: `bot.mc_servers["BMC"]`.

**Async-First:**
Alle I/O-Operationen sind async. Blockierende Operationen (Dateisystem, Subprozesse) verwenden `asyncio.to_thread()`. SQLite via aiosqlite mit WAL-Modus fuer concurrent reads.

### 14.2 Sicherheits-Patterns

**Permission-Hierarchie:**
4-stufiges System (Owner > Admin > Spieler > Everyone). Decorators fuer Commands, Checks in Web-Routes. Ephemeral Fehlermeldungen bei fehlenden Berechtigungen.

**Pfad-Traversal-Schutz:**
Alle Backup/Restore-Operationen validieren Pfade gegen Directory-Traversal.

**Rate-Limiting:**
- Discord: Anti-Spam (5 Nachrichten/10s)
- Web: IP-basiert pro Route
- E-Mail: 1 pro Typ pro 15min
- CurseForge: 2s Minimum zwischen Requests

**CSRF-Schutz:**
Token in Session, Validierung bei POST/PUT/DELETE. OAuth State-Token gegen CSRF.

### 14.3 Fehlerbehandlung

**Graceful Degradation:**
Module pruefen optionale Dependencies (`try: import ts3`) und deaktivieren Features statt abzustuerzen. Monitor-Bot laeuft weiter auch wenn einzelne Checks fehlschlagen.

**Selftest System:**
Pre-Boot-Verifikation prueft: Config-Dateien, ENV-Variablen, Pfad-Berechtigungen, Dependencies, DNS-Aufloesung, Daten-Verzeichnisse. Bot startet nur wenn kritische Checks bestehen.

**Graceful Shutdown:**
SIGTERM/SIGINT Handler mit LIFO-Cleanup. 5s Timeout pro Callback, 15s Gesamt-Timeout. Zweiter Signal = sofortiger Exit.

### 14.4 Datenbank-Patterns

**Einzelne Connection pro Prozess:**
Globale aiosqlite-Connection mit asyncio.Lock. WAL-Modus erlaubt concurrent reads bei single-writer.

**Idempotente Migrationen:**
Jede Migration prueft via `CREATE TABLE IF NOT EXISTS`. Schema-Version via PRAGMA user_version.

**JSON-zu-SQLite Migration:**
Einmaliger Import mit Deduplizierung. Original-JSON bleibt erhalten als Fallback.

### 14.5 Monitoring-Patterns

**Health Auto-Restart:**
3 aufeinanderfolgende Fehler -> Restart. 30min Cooldown zwischen Restarts. Unterdrueckbar waehrend geplanter Wartung. UDP/TCP Dual-Probe.

**Alert-Deduplizierung:**
Verhindert Notification-Spam durch Cooldown-Intervalle pro Alert-Typ.

**Crash Replay:**
Ring-Buffer haelt letzte 50 Log-Zeilen. Bei Crash wird Buffer in Datei gesichert fuer Post-Mortem-Analyse.

**Ressourcen-Forecasting:**
Lineare Regression ueber 30 Tage Metriken-Historie. Prognostiziert Disk/RAM-Erschoepfung in Tagen.

### 14.6 Update-System

**8-Phasen-Update-Orchestrierung (MC):**
Crash-Recovery -> Countdown -> HAR-Unterdrueckung -> Disk-Check -> Download+Hash -> Atomic Swap -> Startversuche -> Health-Check. Jede Phase hat Rollback-Faehigkeit.

**Atomic Swap:**
Staging-Verzeichnis wird vorbereitet, dann per `sudo` atomar mit Produktion getauscht. Altes Verzeichnis wird als Rollback behalten.

**Rollback-System:**
Pre-Update-Backup + alter Stand werden behalten. Rollback per Command oder automatisch bei Health-Check-Fehler. Rotation haelt max 2 Rollback-Verzeichnisse.

### 14.7 Bekannte Besonderheiten

**Deutsche Benutzersprache, englischer Code:**
Alle Discord-Nachrichten, Fehlermeldungen und Embed-Texte sind Deutsch. Variablen, Funktionsnamen und Kommentare sind Englisch.

**Bot-Status-JSON-Dateien:**
Jeder Bot schreibt alle 30s seinen Status in `data/{bot_id}/bot_status.json`. Dashboard liest diese Dateien fuer die Uebersicht. Atomarer Write (Temp -> Rename).

**Persistent Discord Views:**
Buttons und Select-Menus ueberleben Bot-Restarts durch `custom_id`-Prefix-Registrierung. Views werden beim Cog-Load aus der Datenbank wiederhergestellt.

**Shared Database:**
Alle 3 Bots + Dashboard teilen dieselbe SQLite-Datenbank. WAL-Modus ermoeglicht concurrent access. Jeder Bot hat eigene Daten-Verzeichnisse fuer Status-JSON.

**Config Hot-Reload:**
`config.json` kann zur Laufzeit ueber Dashboard oder `/reload` neu geladen werden. Aenderungen werden sofort wirksam ohne Bot-Restart.

**OneDrive-Integration:**
Backups werden via `rclone` zu OneDrive hochgeladen. Rotation haelt max 10 Cloud-Backups. Optionale GPG-Verschluesselung fuer Config-Backups.

---

*Dokumentation generiert am 07.04.2026 aus dem Discord_Bots Projekt v4.1.0*
