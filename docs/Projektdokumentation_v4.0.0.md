# Discord Bot System — Projektdokumentation v4.0.0

> **Version:** 4.0.1 | **Datum:** 12. Maerz 2026 | **Autor:** Marco
> **Vorgaenger:** v4.0.0 (22. Feb 2026) | **Upgrade-Review:** docs/REVIEW_v4.0.0.md
> **Aenderungen:** BMC5-Migration (NeoForge 1.21.1), Chat-Bridge NeoForge-Kompatibilitaet, Bug-Fixes

---

## Inhaltsverzeichnis

1. [Projektuebersicht](#1-projektuebersicht)
   - 1.1 Eckdaten
   - 1.2 Unterstuetzte Gameserver
   - 1.3 Neuerungen in v4.0.0
2. [Architektur](#2-architektur)
   - 2.1 Bot-Aufteilung
   - 2.2 Satisfactory-Architektur
   - 2.3 Minecraft Multi-Server Architektur
   - 2.4 Web-Dashboard Architektur
   - 2.5 Datenbank-Architektur (SQLite)
   - 2.6 Monitoring-Architektur
   - 2.7 Modul-Uebersicht
3. [Satisfactory — Detailbeschreibung](#3-satisfactory--detailbeschreibung)
   - 3.1 Server-Steuerung (systemd)
   - 3.2 HTTPS API-Client
   - 3.3 Savegame-System
   - 3.4 Blueprint-Management
   - 3.5 Whitelist & Blacklist
   - 3.6 Settings-Backup
   - 3.7 Update-Mechanismus (SteamCMD)
4. [Minecraft — Detailbeschreibung](#4-minecraft--detailbeschreibung)
   - 4.1 Multi-Server Architektur
   - 4.2 RCON-Client
   - 4.3 Chat-Bridge
   - 4.4 Backup-System
   - 4.5 Update-Checker (Paper API)
   - 4.6 Blacklist-System
   - 4.7 Modpack-Update-Check
   - 4.8 World-Analyse
   - 4.9 IP-Ban System (UFW)
   - 4.10 Ankuendigungs-Banner
5. [Slash Commands — Vollstaendige Referenz](#5-slash-commands--vollstaendige-referenz)
   - 5.1 Satisfactory-Commands (GameServer Bot)
   - 5.2 Minecraft-Commands (GameServer Bot)
   - 5.3 Allgemeine Commands (GameServer Bot)
   - 5.4 Monitor Bot Commands
   - 5.5 Admin Bot Commands
6. [Monitoring & Automatisierung](#6-monitoring--automatisierung)
   - 6.1 Health Checks + Auto-Restart (F27)
   - 6.2 Auto-Backup
   - 6.3 Daily Restart
   - 6.4 Player-Tracking
   - 6.5 Update-Checks
   - 6.6 Crash Replay (F30)
   - 6.7 Selftest (F62)
   - 6.8 Service Watchdog (F50)
   - 6.9 Disk Guard (F49)
   - 6.10 DuckDNS Monitor (F51)
   - 6.11 Port Monitor (F52)
   - 6.12 SSL Monitor (F32)
   - 6.13 Fail2Ban Integration (F31)
   - 6.14 Backup Integrity (F33)
   - 6.15 Package Checker (F42)
   - 6.16 Ressourcen-Forecasting (F37)
   - 6.17 Graceful Degradation (F59)
   - 6.18 Alert-Deduplizierung (F54)
   - 6.19 Statistics Collector (F57)
   - 6.20 Weitere Monitoring-Features
   - 6.21 Scheduled Messages
7. [Web-Dashboard](#7-web-dashboard)
   - 7.1 Architektur
   - 7.2 Authentifizierung
   - 7.3 Middleware-Stack
   - 7.4 Routen-Uebersicht
   - 7.5 SSE Live-Updates (F29)
   - 7.6 Dashboard-Suche (F55)
   - 7.7 Analytics-Dashboard (F58)
   - 7.8 Korrelations-Dashboard (F35)
   - 7.9 Export-Funktionen (F36)
   - 7.10 Changelog-Seite (F45)
   - 7.11 Error-Dashboard (F44)
   - 7.12 Webhook-Integration (F60)
   - 7.13 Dark Mode (F46)
   - 7.14 Health-API (F34)
8. [Datenbank (SQLite)](#8-datenbank-sqlite)
   - 8.1 Migration von JSON (F28)
   - 8.2 Schema & Tabellen
   - 8.3 FTS5 Volltextsuche (F55)
   - 8.4 Backup & Rotation (F63)
   - 8.5 Data Retention (F56)
   - 8.6 Config-Versionierung (F53)
9. [ENV-Variablen Referenz](#9-env-variablen-referenz)
   - 9.1 Discord
   - 9.2 Satisfactory
   - 9.3 Minecraft Multi-Server
   - 9.4 Backup & Cloud
   - 9.5 E-Mail
   - 9.6 Web-Status
   - 9.7 Modpack-Updates
   - 9.8 Admin Bot
   - 9.9 TeamSpeak
   - 9.10 Web-Dashboard
   - 9.11 Monitoring (Neu in v4.0.0)
10. [Server-Infrastruktur](#10-server-infrastruktur)
    - 10.1 Hardware
    - 10.2 Dienste und Ports
    - 10.3 RAM-Aufteilung
    - 10.4 systemd Services
    - 10.5 SSH-Zugang
    - 10.6 Deployment-Workflow
11. [Sicherheit](#11-sicherheit)
12. [Entwicklungshistorie](#12-entwicklungshistorie)
13. [Konfigurationsdateien](#13-konfigurationsdateien)
14. [Abschluss](#14-abschluss)

---

## 1. Projektuebersicht

Das Discord Bot System ist ein Drei-Bot-System zur Verwaltung von Gameservern ueber Discord. Es steuert einen Satisfactory Dedicated Server sowie zwei Minecraft-Server (Vanilla/Paper + Better MC) auf einem dedizierten Linux-Server. Ein interaktives Web-Dashboard mit Admin-Oberflaeche bietet Serververwaltung, Echtzeit-Monitoring und Volltextsuche. Der Admin Bot uebernimmt Discord-Moderation, Community-Features und TeamSpeak-Integration. Seit v4.0.0 verfuegt das System ueber eine zentrale SQLite-Datenbank, umfassende Monitoring-Module und ein sicherheitsgehaertetes Web-Dashboard.

### 1.1 Eckdaten

- 164 Python-Dateien, ca. 40.000+ Zeilen Code
- 3 Discord-Bots, 27 Cogs, 80+ Module, 7 Utils
- ca. 100+ Slash Commands
- Web-Dashboard mit FastAPI + HTMX + Jinja2 (20 Routes, 30 Templates)
- SQLite-Datenbank mit WAL-Modus, 31 Tabellen, FTS5 Volltextsuche
- 5 Middleware-Layer (Session, Timeout, CSRF, Rate-Limiter, CORS)
- Python 3.10+ mit discord.py 2.x
- Vollstaendig async (asyncio)
- systemd-Integration fuer alle Dienste
- 39 neue Features in v4.0.0 (F27-F65)

### 1.2 Unterstuetzte Gameserver

Der GameServer Bot (Bot 1) steuert alle Server via Slash Commands. Der Monitor Bot (Bot 2) uebernimmt automatisierte Background-Tasks wie Health Checks, Backups, Chat-Bridges, Monitoring und Dashboard-Updates. Der Admin Bot (Bot 3) haelt Moderation, Community-Features, TeamSpeak-Verwaltung und Temp Voice Channels vor.

**Satisfactory:** Dedicated Server mit Steuerung ueber die offizielle HTTPS API (Port 7777). Bietet Savegame-Analyse mit Binary-Header-Parsing, Blueprint-Management mit Kategorien, Whitelist/Blacklist-System, Settings-Backup via API, und automatische Updates via SteamCMD. Health-Check mit Auto-Restart (F27).

**Minecraft Vanilla/Paper:** Paper MC 1.21.4 Build 209. Steuerung ueber systemd + RCON (Port 25576 lokal). Bidirektionale Chat-Bridge via Log-Polling und RCON. Paper API Update-Check. World-Analyse mit NBT-Parsing. Health-Check mit Auto-Restart (F27).

**Minecraft Better MC 5:** BMC5 NeoForge Modpack (MC 1.21.1). Steuerung ueber systemd + RCON (Port 25575 lokal). Bidirektionale Chat-Bridge. Automatischer Modpack-Update-Check via Modrinth/CurseForge API (alle 12 Stunden). IP-Ban via UFW-Integration. Health-Check mit Auto-Restart (F27).

### 1.3 Neuerungen in v4.0.0

v4.0.0 ist ein Major-Upgrade mit 39 neuen Features in 5 Phasen:

**Phase 1 — Kern-Infrastruktur (14 Features):**
Graceful Shutdown (F61), Pre-Boot Selftest (F62), CSRF-Schutz (F64), Session-Timeout (F65), Health Auto-Restart (F27), Disk Guard (F49), Service Watchdog (F50), DuckDNS Monitor (F51), Port Monitor (F52), Fail2Ban Integration (F31), SSL Monitor (F32), Backup Integrity (F33), Health-API (F34), Rate Limiter (F48).

**Phase 2 — Datenbank (4 Features):**
SQLite Migration (F28), Data Retention (F56/F63), Backup-Rotation (F56), Config-Versionierung (F53).

**Phase 3 — Dashboard-Erweiterungen (9 Features):**
SSE Live-Updates (F29), Korrelations-Dashboard (F35), Export-Funktionen (F36), Ressourcen-Forecasting (F37), Error-Dashboard (F44), Dashboard-Suche (F55), Stats Collector (F57), Analytics-Dashboard (F58), Changelog-Seite (F45).

**Phase 4 — Bot-Features (7 Features):**
Crash Replay (F30), Moderation-Erweiterungen (F39), Leveling-System (F40), Giveaway-System (F41), Custom Commands (F43), Alert-Deduplizierung (F54), Graceful Degradation (F59).

**Phase 5 — Feinschliff (5 Features):**
Maintenance Mode (F38), Paket-Checker (F42), Dark Mode (F46), Performance-Optimierung (F47), Webhook-Integration (F60).

---

## 2. Architektur

### 2.1 Bot-Aufteilung

**GameServer Bot (Bot 1):** Verarbeitet alle Slash Commands der Benutzer. Startet, stoppt und verwaltet Server. Fuehrt RCON-Befehle aus. Verwaltet Backups, Whitelist, Blacklist und Blueprints. Cooldown-Management verhindert Spam bei kritischen Commands. Token: `DISCORD_TOKEN_MANAGER`. 6 Cogs.

**Monitor Bot (Bot 2):** Fuehrt automatisierte Background-Tasks aus. Health Checks alle 2 Minuten (F27). Performance-Monitoring alle 5 Minuten. Dashboard-Embed alle 10 Minuten. Auto-Backups alle 6 Stunden. Daily Restart um 04:00. Chat-Bridge fuer Minecraft. Player-Tracking. Update-Checks. Crash-Detection mit Auto-Restart. Statistics Collector (F57). StatusWriter fuer JSON-Bridge zum Dashboard. Service Watchdog (F50). Disk Guard (F49). DuckDNS Monitor (F51). Port Monitor (F52). SSL-Check. DB-Maintenance (F63). Package Checker (F42). Search Indexer (F55). Token: `DISCORD_TOKEN_WATCHDOG`. 4 Cogs.

**Admin Bot (Bot 3):** Haelt Discord-Moderation, Community-Features und TeamSpeak-Steuerung vor. Cogs: moderation, warn, reaction_roles, leveling, tickets, audit, giveaway, temp_voice, teamspeak, server_backup, embed_sender, custom_commands, profile, notify, welcome, command_stats. Token: `ADMIN_BOT_TOKEN`. Intents: Members, Message Content, Reactions. 16 Cogs.

### 2.2 Satisfactory-Architektur

Der Satisfactory-Server wird ueber HTTPS API (Port 7777) und systemd gesteuert. Die API-Kommunikation laeuft ueber aiohttp mit konfigurierbarer SSL-Verifizierung. Health-Check (F27) prueft zusaetzlich UDP-Port 15777 (ServerQueryPort).

Module: `server.py`, `api_client.py`, `whitelist.py`, `blacklist.py`, `blueprint_manager.py`, `savegame_stats.py`, `savegame_analyzer.py`, `settings_backup.py`, `save_header.py`.

### 2.3 Minecraft Multi-Server Architektur

Jeder MC-Server wird ueber `MC_{SERVER_ID}_*` ENV-Variablen konfiguriert. Server-IDs: BMC und VANILLA.

Aktuelle Server:
- **BMC** — Better MC 5 Modpack auf NeoForge 1.21.1 (NeoForge 21.1.217), ca. 200 Mods. Port 25566 (Game), Port 25575 (RCON). Pfad: `/home/minecraft/bmc5/`. RAM: Xms4G / Xmx12G mit G1GC. Gestartet via `run.sh` (NeoForge-Installer generiert).
- **VANILLA** — Minecraft Vanilla 1.21.1. Port 25565 (Game), Port 25576 (RCON). Pfad: `/home/minecraft/vanilla/`. Derzeit offline.

Pro Server Instanzen: Server-Steuerung, Backup, Chat-Bridge, Player-Tracking, Crash-Replay (F30), Stats-Tracking. Health-Check (F27) prueft RCON-Erreichbarkeit.

### 2.4 Web-Dashboard Architektur

FastAPI + HTMX + Jinja2 Admin-Oberflaeche. Authentication: Discord OAuth2 oder bcrypt Fallback-Login. Port 8080 (intern), Port 443 (extern via Nginx Reverse-Proxy). 20 Route-Module, 30 Templates (11 Seiten + 19 Partials). Dark-Theme (F46). Statistics Collector als Hintergrund-Task. SSE Live-Updates (F29). FTS5 Volltextsuche (F55). 5-Layer Middleware-Stack.

### 2.5 Datenbank-Architektur (SQLite)

Seit v4.0.0 zentrale SQLite-Datenbank (`data/botdata.db`) als Ersatz fuer alle JSON-Dateispeicher. WAL-Modus fuer concurrent Access. 31 Tabellen inkl. FTS5-Index. Zugriff ausschliesslich ueber `modules/database/db_manager.py` via `aiosqlite`.

Komponenten:
- `db_manager.py` — Connection-Pool, WAL-Modus, Foreign Keys (F28)
- `migrations.py` — Schema-Versionierung und automatische Migration
- `models.py` — Datenmodelle
- `json_importer.py` — Einmalige Migration von JSON nach SQLite
- `maintenance.py` — Backup + Retention (F63/F56)
- `search_indexer.py` — FTS5 Volltextsuche (F55)

### 2.6 Monitoring-Architektur

Der Monitor Bot orchestriert alle Monitoring-Module. Jedes Modul laeuft als asyncio Background-Task und kommuniziert ueber Callbacks:

```
Monitor Bot (Orchestrator)
├── HealthAutoRestart (F27) ─── UDP/TCP Probes → Auto-Restart
├── ServiceWatchdog (F50)  ─── systemd Status → Restart bei Failure
├── DiskGuard (F49)        ─── Disk-Usage → 3-Stufen-Warnung
├── PortMonitor (F52)      ─── TCP Connect → Port-Status
├── DuckDNSMonitor (F51)   ─── DNS Resolve → IP-Vergleich
├── SSLMonitor (F32)       ─── Zertifikat-Ablauf → Warnung
├── PackageChecker (F42)   ─── apt → Security-Updates
├── StatsCollector (F57)   ─── System-Metriken → SQLite
├── Forecasting (F37)      ─── Lineare Regression → Vorhersagen
├── CrashReplay (F30)      ─── Log-Kontext bei Crashes
├── GracefulDegradation (F59) ── Feature-Toggle bei Fehlern
├── StatusWriter            ─── JSON-Bridge → Dashboard
└── DatabaseMaintenance     ─── Backup + Retention (alle 6h)
```

**JSON-Bridge Pattern:** Der StatusWriter schreibt Monitoring-Daten als JSON-Dateien nach `data/monitor/`. Das Dashboard liest diese ueber API-Endpunkte. Dieses Pattern entkoppelt Monitor Bot und Web-Dashboard vollstaendig.

| JSON-Datei | Schreiber | Leser |
|------------|-----------|-------|
| satisfactory_status.json | StatusWriter | health_route, dashboard |
| mc_bmc_status.json | StatusWriter | health_route, server_detail |
| port_monitor.json | StatusWriter | health_route |
| disk_guard.json | StatusWriter | health_route |
| service_watchdog.json | StatusWriter | health_route |
| duckdns_monitor.json | StatusWriter | health_route |
| health_auto_restart.json | StatusWriter | health_route |
| ssl_status.json | StatusWriter | security_route |
| package_checker.json | StatusWriter | system_route |
| events.json | StatusWriter | dashboard, errors_route |

### 2.7 Modul-Uebersicht

| Paket | Anzahl | Beschreibung |
|-------|--------|-------------|
| modules/satisfactory/ | 9 | SAT Server-Steuerung, API, Savegames, Blueprints |
| modules/minecraft/ | 9 | MC Server-Steuerung, RCON, Chat-Bridge, Backups |
| modules/monitoring/ | 21 | Health, Stats, Watchdog, Forecasting, Crash Replay |
| modules/database/ | 7 | SQLite Manager, Migrations, Search, Maintenance |
| modules/system/ | 3 | Disk Guard, Package Checker |
| modules/network/ | 3 | DuckDNS, Port Monitor |
| modules/security/ | 4 | Fail2Ban, SSL Monitor, Ban Manager |
| modules/backup/ | 5 | Backup Manager, Integrity, OneDrive, Config Backup |
| modules/notifications/ | 2 | E-Mail, Discord Notifications |
| modules/teamspeak/ | 4 | ServerQuery Client, Chat-Bridge, Channels |
| modules/ (Root) | 20 | Alert Dedup, Config History, Leveling, Tickets, etc. |
| utils/ | 7 | Config, Logger, Permissions, Formatting, Selftest, Shutdown |
| cogs/ | 27 | Discord Slash Commands fuer alle 3 Bots |
| web/routes/ | 20 | Dashboard API-Endpunkte und Seiten |
| web/middleware/ | 4 | CSRF, Rate Limiter, Session Timeout |
| web/templates/ | 30 | HTML Templates (Jinja2) |

---

## 3. Satisfactory — Detailbeschreibung

### 3.1 Server-Steuerung (systemd)

Klasse SatisfactoryServer. systemctl-Aufrufe nutzen ALLOWED_ACTIONS Whitelist. Server lauft unter Linux-User "satisfactory". Operationen: `is_running()`, `start()`, `stop()`, `restart()`, `get_status()`.

### 3.2 HTTPS API-Client

Klasse SatisfactoryAPI. POST-basiert, Port 7777. Bearer-Token Authentifizierung. Daten in Dataclasses (ServerState, HealthInfo). Funktionen: `query_server_state()`, `get_server_options()`, `get_advanced_game_settings()`, `save_game()`, `load_game()`, `set_admin_password()`, `kick_player()`, `get_player_list()`.

### 3.3 Savegame-System

Drei Module: `save_header.py` (Binary Parser), `savegame_stats.py` (Auflistung), `savegame_analyzer.py` (Tiefenanalyse via satisfactory-save Package).

### 3.4 Blueprint-Management

Verwaltet Blueprints im `/home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames/blueprints/` Verzeichnis. Upload via ZIP oder 2 Einzeldateien. 6 Kategorien. Metadaten in JSON-DB. Operationen: Upload, Download, List, Delete.

### 3.5 Whitelist & Blacklist

Zwei Module (WhitelistManager, BlacklistManager). JSON-Dateien. async Load/Save, Spieler hinzufuegen/entfernen, Liste anzeigen, Toggle.

### 3.6 Settings-Backup

Klasse SettingsBackup. Sichert Server-Einstellungen via API. Backups mit Zeitstempel. Restore sendet gesicherte Settings zurueck.

### 3.7 Update-Mechanismus (SteamCMD)

UpdateChecker prueft alle 6 Stunden via SteamCMD Build-ID. Benachrichtigung bei neuer Version. Manual Update via `/sat update` Command. SteamChangelog fuer Changelog-Abrufe. Automatische Updates nicht enabled.

---

## 4. Minecraft — Detailbeschreibung

### 4.1 Multi-Server Architektur

Prefix-basiertes ENV-System (`MC_{SERVER_ID}_*`). Pro Server automatische Instanzen bei Bot-Start.

### 4.2 RCON-Client

Async RCON-Client mit signed 32-bit Integers, Reconnect, `asyncio.Lock`. Context Manager: `async with MinecraftRCON(...) as rcon`.

### 4.3 Chat-Bridge

Bidirektionale Bridge mit Log-Polling alle 5 Sekunden. Regex-basierte Erkennung: Chat, Join/Leave, Advancements, Deaths. Mention-Injection-Schutz via `AllowedMentions.none()`. RCON-Injection-Schutz.

Unterstuetzt zwei Log-Formate:
- **Vanilla:** `[HH:MM:SS] [Server thread/INFO]: Nachricht`
- **NeoForge:** `[DDMonatJJJJ HH:MM:SS.ms] [Server thread/INFO] [logger.name/]: Nachricht`

Die Regex-Patterns verwenden flexible Bausteine: `_TS` (beliebiger Zeitstempel in eckigen Klammern) und `_TH` (Server-Thread + optionale Logger-Tags). Death-Messages werden automatisch mit deutschen Mob-Namen angezeigt (`translate_mob_names()` mit Word-Boundary-Matching). Discord→MC-Richtung via RCON `/say` mit Rate-Limiting (1 Call/Sekunde).

### 4.4 Backup-System

World-Backup-Manager. async I/O, Path-Traversal-Schutz. Vor Backup: `save-all` via RCON.

### 4.5 Update-Checker (Paper API)

MinecraftUpdateChecker vergleicht Paper-Build via Paper API. Nur Vanilla/Paper, nicht BMC.

### 4.6 Blacklist-System

Klasse MinecraftBlacklist. Serveruebergreifend. Auto-Durchsetzung via RCON. Ban-Historie mit Grund, Zeitstempel, Admin. JSON-Persistenz. Commands: `/mc blacklist add/remove/list/history`.

### 4.7 Modpack-Update-Check

ModpackUpdater prueft alle 12 Stunden. Modrinth (bevorzugt) + CurseForge (Fallback). Benachrichtigung bei neuer Version. Manual Check via `/mc config modpack_check`.

### 4.8 World-Analyse

`world_analyzer.py` nutzt nbtlib + anvil-parser2. NBT-Parsing. Statistiken: Welt-Groesse, Chunk-Anzahl, Spawn-Punkt, Difficulty. Command: `/mc world stats [server]`. `asyncio.to_thread()` fuer non-blocking.

### 4.9 IP-Ban System (UFW)

`player_ip_tracker.py` kann IPs via UFW blocken. IPv4-Regex-Validierung. Commands: `/mc ipban add/remove <ip>`.

### 4.10 Ankuendigungs-Banner

Erweiterter `/mc say` Command mit Banner-Support. Syntax: `/mc say "<nachricht>" [server] [banner_type] [repeat]`. Banner-Typen: title, subtitle, actionbar. Optional mit Wiederholung.

---

## 5. Slash Commands — Vollstaendige Referenz

### 5.1 Satisfactory-Commands (GameServer Bot)

**Status:** `/sat status` (Alle)
**Backup:** `/sat backup` (Admin), `/sat backups list` (Spieler), `/sat restore` (Owner), `/sat download` (Spieler)
**Stats:** `/sat stats` (Spieler)
**Settings:** `/sat settings` (Spieler), `/sat playerlimit` (Admin), `/sat autosave` (Admin)
**Advanced:** `/sat console` (Owner), `/sat load` (Owner), `/sat update` (Owner)
**Blueprints:** `/sat blueprints upload/list/download/delete` (Admin)
**Players:** `/sat players` (Spieler), `/sat kick/ban/unban` (Admin)
**Whitelist/Blacklist:** `/whitelist add/remove/list`, `/blacklist add/remove/list` (Admin)

Removed in v3.2.0: `/sat start`, `/sat stop`, `/sat restart`, `/sat cancel`, Konfiguration-Commands.

### 5.2 Minecraft-Commands (GameServer Bot)

**Status:** `/mc status [server]` (Alle)
**Players:** `/mc players list/kick/ban [server]` (Admin)
**Whitelist:** `/mc whitelist add/remove/list [server]` (Admin)
**Blacklist:** `/mc blacklist add/remove/list/history` (Admin)
**Backup:** `/mc backup create/list/restore` (Spieler/Owner)
**Admin:** `/mc command <cmd>` (Owner), `/mc say` (Admin), `/mc world stats` (Spieler)

Removed in v3.2.0: `/mc start`, `/mc stop`, `/mc restart`, `/mc cancel`, In-Game-Commands, Konfiguration.

### 5.3 Allgemeine Commands (GameServer Bot)

`/help` (Alle, rollenbasiert F26), `/reload <cog>` (Owner), `/clear` (Admin), `/timeout` (Admin), `/schedule add/list/cancel` (Admin).

### 5.4 Monitor Bot Commands

`/performance`, `/dashboard`, `/stats`, `/report`, `/mcstats`, `/mcreport`, `/mccrashlog`, `/scheduler`, `/update check`, `/email test|status`, `/onedrive status|upload|list`, `/backup stats`, `/maintenance on|off` (F38).

### 5.5 Admin Bot Commands

**Moderation:** `/mod warn/warnlist/unwarn/mute/unmute/kick/ban/wordfilter`
**Reaction Roles:** `/reactionrole add/remove/list`
**Leveling:** `/level`, `/leaderboard`, `/levelconfig`
**Tickets:** `/ticket create/close/setup`
**Giveaway:** `/giveaway start/end/reroll`
**Temp Voice:** `/tempvoice setup/config`
**TeamSpeak:** `/ts status/users/channels/message`
**Server-Backup:** `/serverbackup create/restore/list`
**Custom Commands (F43):** `/customcmd add/remove/list/edit`
**Embeds:** `/embed send/edit`
**Profile:** `/profile view/set`
**Notify:** `/notify setup/test`

---

## 6. Monitoring & Automatisierung

### 6.1 Health Checks + Auto-Restart (F27)

**Modul:** `modules/monitoring/health_checker.py`

Intelligenter Health-Check fuer alle Gameserver. Unterscheidet zwischen "Prozess laeuft" (systemd) und "API erreichbar" (Query/RCON).

**SAT Health-Check:** UDP-Probe an Port 15777 (ServerQueryPort). Timeout: 20 Sekunden.
**MC Health-Check:** TCP-Verbindung zu RCON-Port. Timeout: 10 Sekunden.

**Auto-Restart Ablauf:**
1. Health-Check schlaegt X-mal hintereinander fehl
2. Benachrichtigung im Admin-Channel
3. `systemctl restart {service}` ausfuehren
4. 2 Minuten warten, erneut pruefen
5. Erfolgs- oder Fehler-Meldung

**Konfiguration (config.json):**
- `health_auto_restart_failures`: 10 (erlaubt ~25 Min Boot-Zeit)
- `health_auto_restart_timeout`: 20 Sekunden
- `health_check_interval`: 150 Sekunden
- Cooldown: Max 1 Auto-Restart pro 30 Minuten pro Server

**Callbacks:** Discord-Benachrichtigungen an `ADMIN_LOG_CHANNEL_ID`. StatusWriter-Integration fuer Dashboard.

### 6.2 Auto-Backup

SAT: Alle 6h Savegame-Backup. Lokal + OneDrive (verpflichtend).
MC: Alle 6h World-Backup. `save-all` vor Backup. Lokal + OneDrive.

### 6.3 Daily Restart

04:00 Uhr fuer alle Server. Nur wenn >12h runtime. Skip wenn Spieler online.

### 6.4 Player-Tracking

Pro Server Tracker. Join/Leave Events. Spielzeit. Wochenberichte. Persistenz in SQLite (seit v4.0.0).

### 6.5 Update-Checks

SAT: 6h via SteamCMD.
MC Vanilla: 6h via Paper API.
BMC: 12h via Modrinth/CurseForge API.

### 6.6 Crash Replay (F30)

**Modul:** `modules/monitoring/crash_replay.py`

Erfasst Log-Kontext (letzte 50 Zeilen) bei Server-Crashes. Analyse + Zusammenfassung im Admin-Channel. Hilft bei der Diagnose von wiederkehrenden Problemen.

### 6.7 Selftest (F62)

**Modul:** `utils/selftest.py`

Pre-Boot Verifikation bei jedem Bot-Start. 17+ Checks: Discord-Token, SAT API, SAT Prozess, UFW, Disk, Savegame-Pfade, OneDrive, E-Mail, SteamCMD, Config, Pro MC-Server: Status, RCON, Log-Pfad, Backup-Pfad. Fehlschlagende Checks werden geloggt, verhindern aber nicht den Start (Graceful Degradation).

### 6.8 Service Watchdog (F50)

**Modul:** `modules/monitoring/service_watchdog.py`

Ueberwacht systemd-Services und startet ausgefallene Services automatisch neu. Konfigurierbare Service-Liste. Max 3 Restarts pro Stunde (Cooldown-Schutz). Callbacks: `on_service_down`, `on_restart_success`, `on_restart_failed`, `on_cooldown_reached`.

### 6.9 Disk Guard (F49)

**Modul:** `modules/system/disk_guard.py`

3-Stufen Disk-Space-Ueberwachung:
- **Stufe 1 (<20% frei):** Warnung im Admin-Channel
- **Stufe 2 (<10% frei):** Automatisches Cleanup (alte Logs, Backups)
- **Stufe 3 (<5% frei):** Kritischer Alarm

Callbacks: `on_warning`, `on_cleanup`, `on_critical`. Dashboard-Integration via StatusWriter.

### 6.10 DuckDNS Monitor (F51)

**Modul:** `modules/network/duckdns_monitor.py`

Taegliche DNS-Verifikation. Vergleicht die bei DuckDNS hinterlegte IP mit der tatsaechlichen Server-IP. Bei Abweichung: Kritische Warnung + optionales Auto-Update (wenn `DUCKDNS_TOKEN` gesetzt). Callback: `on_mismatch`.

### 6.11 Port Monitor (F52)

**Modul:** `modules/network/port_monitor.py`

TCP-Connect-Tests alle 5 Minuten fuer konfigurierte Ports. Prueft Erreichbarkeit von Gameserver-Ports, Dashboard, SSH etc. Callbacks: `on_port_closed`, `on_port_recovered`. Dashboard-Integration.

### 6.12 SSL Monitor (F32)

**Modul:** `modules/security/ssl_monitor.py`

Ueberwacht SSL/TLS-Zertifikate. Warnung bei 14 Tagen Restlaufzeit. Kritischer Alarm bei 3 Tagen. Dashboard-Anzeige ueber `ssl_status.json`.

### 6.13 Fail2Ban Integration (F31)

**Modul:** `modules/security/fail2ban.py`

Liest Fail2Ban-Status (aktive Jails, gebannte IPs) ueber async Subprocess-Aufrufe. Ermoeglicht Ban/Unban ueber Discord-Commands. Dashboard-Anzeige auf der Security-Seite.

### 6.14 Backup Integrity (F33)

**Modul:** `modules/backup/integrity.py`

Prueft Backup-Dateien auf Integritaet: SHA256-Checksummen, tar.gz-Strukturtest, Groessen-Plausibilitaet. Laeuft automatisch nach jedem Backup.

### 6.15 Package Checker (F42)

**Modul:** `modules/system/package_checker.py`

Woechentlicher Check auf verfuegbare System-Updates (`apt update` + `apt list --upgradable`). Unterscheidet zwischen normalen und Security-Updates. Ergebnisse in SQLite und Dashboard (system_route). Discord-Benachrichtigung bei Security-Updates.

### 6.16 Ressourcen-Forecasting (F37)

**Modul:** `modules/monitoring/forecasting.py`

Vorhersage von Ressourcen-Erschoepfung (Disk, RAM) mittels linearer Regression auf historische Metriken. Berechnet Zeitpunkt bis zur Erschoepfung. Dashboard-Anzeige ueber `forecast_route.py`.

### 6.17 Graceful Degradation (F59)

**Modul:** `modules/monitoring/graceful_degradation.py`

Faengt Fehler in nicht-kritischen Subsystemen ab und deaktiviert diese temporaer, statt den gesamten Bot abstuerzen zu lassen. Automatisches Re-Enabling nach konfigurierbarem Timeout.

### 6.18 Alert-Deduplizierung (F54)

**Modul:** `modules/alert_dedup.py`

Verhindert doppelte Benachrichtigungen innerhalb eines konfigurierbaren Zeitfensters. Tracked gesendete Alerts in SQLite (`alerts_sent` Tabelle). Resolved-Alerts werden nach 30 Tagen automatisch bereinigt (F56).

### 6.19 Statistics Collector (F57)

**Modul:** `modules/monitoring/stats_collector.py`

Sammelt alle 5 Minuten System- und Server-Metriken: CPU, RAM, Disk, Netzwerk, Spieler, Uptime, Ping. Speichert in SQLite `stats_history` Tabelle (90 Tage Retention). Basis fuer Analytics-Dashboard (F58) und Forecasting (F37).

### 6.20 Weitere Monitoring-Features

**Performance (5min):** CPU, RAM, Disk-Metriken. **Dashboard-Embed (10min):** Status-Update im Discord-Channel. **Voice-Stats (5min):** Voice-Channel Statistiken. **Login Audit:** Dashboard-Login-Protokollierung. **Auto-Cleanup:** Alte Logs und Dateien bereinigen. **Savegame Protection:** Schreibschutz waehrend kritischer Operationen. **Server Optimizer:** Performance-Optimierungen.

### 6.21 Scheduled Messages

SchedulerCog. Relative/absolute Zeitangaben. Wiederholungen: einmalig, taeglich, woechentlich. Max 20 aktiv. Persistiert in SQLite.

---

## 7. Web-Dashboard

### 7.1 Architektur

FastAPI + HTMX + Jinja2. Uvicorn ASGI Server auf Port 8080. Nginx Reverse-Proxy auf Port 443 (HTTPS via Let's Encrypt). WebSocket-Support fuer Echtzeit-Updates. 20 Route-Module, 30 Templates.

### 7.2 Authentifizierung

Zwei Login-Methoden:
1. **Discord OAuth2** (bevorzugt): Guild- und Rollen-Pruefung. Redirect-basiert.
2. **bcrypt Fallback**: Admin-Login mit `WEB_ADMIN_USER` / `WEB_ADMIN_PASS_HASH` aus .env.

Sessions: Starlette SessionMiddleware mit `WEB_SECRET_KEY`. Max-Age: 24 Stunden. Remember-Me: 7 Tage. httpOnly Cookies.

### 7.3 Middleware-Stack

Starlette verarbeitet Middleware in LIFO-Reihenfolge (letzte `add_middleware()` = aeusserste Schicht = laeuft zuerst):

| Reihenfolge | Middleware | Feature | Beschreibung |
|-------------|-----------|---------|-------------|
| 1 (aeusserste) | SessionMiddleware | — | Session-Verwaltung, stellt `request.session` bereit |
| 2 | SessionTimeoutMiddleware | F65 | 60 min Inaktivitaet, 24h absolut, 7d Remember-Me |
| 3 | CSRFMiddleware | F64 | Token-Validierung fuer POST/PUT/DELETE/PATCH |
| 4 | RateLimitMiddleware | F48 | Token-Bucket: Login 5/min, Actions 10/min, Read 60/min |
| 5 (innerste) | CORSMiddleware | — | Cross-Origin Resource Sharing |

**CSRF-Exempt:** `/auth/login`, `/auth/discord/callback`, `/api/health`, `/ws`
**Rate-Limit-Exempt:** `/static`, `/favicon.ico`, `/ws`
**Timeout-Exempt:** `/auth/*`, `/api/health`, `/static`, `/ws`

### 7.4 Routen-Uebersicht

| Route | Datei | Beschreibung |
|-------|-------|-------------|
| `/auth/*` | auth (in app.py) | Login/Logout, Discord OAuth2 |
| `/` | dashboard.py | Hauptseite mit Server-Uebersicht |
| `/errors` | errors_route.py | Fehler-/Warnungs-Uebersicht (F44) |
| `/config` | config_route.py | Konfigurationsmanagement |
| `/system` | system_route.py | System-Status, Package-Updates |
| `/server/<id>` | server_detail.py | Server-Detailansicht + RCON-Konsole |
| `/analytics` | analytics_route.py | Metriken + Charts (F58) |
| `/admin-bot` | admin_bot_route.py | Admin Bot Verwaltung |
| `/security` | security_route.py | Sicherheitsstatus, Fail2Ban |
| `/search` | search_route.py | Volltextsuche (F55) |
| `/changelog` | changelog_route.py | Changelog-Anzeige (F45) |
| `/api/health` | health_route.py | Public Health-Endpoint (F34) |
| `/api/sse/*` | sse_route.py | Server-Sent Events (F29) |
| `/api/forecast/*` | forecast_route.py | Ressourcen-Vorhersagen (F37) |
| `/api/export/*` | export_route.py | CSV-Export (F36) |
| `/api/correlation/*` | correlation_route.py | Korrelationsanalyse (F35) |
| `/api/backup-status` | backup_status_route.py | Backup-Monitoring |
| `/api/config/reload` | config_reload_route.py | Live-Config-Reload |
| `/api/theme` | theme_route.py | Theme-Wechsel |
| `/api/webhook/*` | webhook_route.py | GitHub Webhooks (F60) |

### 7.5 SSE Live-Updates (F29)

**Route:** `web/routes/sse_route.py`

Server-Sent Events fuer Echtzeit-Dashboard-Updates ohne Polling. Heartbeat alle 15 Sekunden. Events: Server-Status-Aenderungen, Spieler-Join/Leave, Alerts. Client-seitig via EventSource API in HTMX.

### 7.6 Dashboard-Suche (F55)

**Route:** `web/routes/search_route.py` | **Template:** `web/templates/search.html`
**Indexer:** `modules/database/search_indexer.py`

FTS5-basierte Volltextsuche ueber alle Dashboard-Daten. Indexiert: Events, Player Sessions, Audit Log, Command Log, Backup History, Custom Commands. Suchhistorie in Session. Ergebnisse gruppiert nach Quelle mit Snippet-Highlights.

**Endpunkte:**
- `GET /search` — Suchseite mit Ergebnissen
- `GET /api/search?q=...` — JSON-API
- `POST /api/search/reindex` — Admin: Index neu aufbauen

**Index-Sync:** Inkrementelles Update alle 6 Stunden via `db_maintenance_task` im Monitor Bot.

### 7.7 Analytics-Dashboard (F58)

**Route:** `web/routes/analytics_route.py`

System-Statistiken und Metriken mit Chart.js Visualisierung. Zeigt CPU, RAM, Disk, Spielerzahlen und Netzwerk-Metriken ueber konfigurierbare Zeitraeume. Basiert auf `stats_history` Tabelle.

### 7.8 Korrelations-Dashboard (F35)

**Route:** `web/routes/correlation_route.py`

REST API fuer Korrelationsanalyse und Anomalie-Erkennung. Korreliert verschiedene Metriken (z.B. CPU-Last vs. Spielerzahl) um Zusammenhaenge zu identifizieren.

### 7.9 Export-Funktionen (F36)

**Route:** `web/routes/export_route.py`

CSV-Export von Dashboard-Daten: Spieler-Sessions, Events, Stats-History, Audit Log. Zeitraum-Filter und Daten-Selektion.

### 7.10 Changelog-Seite (F45)

**Route:** `web/routes/changelog_route.py`

Rendert `CHANGELOG.md` als HTML im Dashboard. Automatische Markdown-zu-HTML-Konvertierung.

### 7.11 Error-Dashboard (F44)

**Route:** `web/routes/errors_route.py`

Fehler- und Warnungs-Uebersicht aus Log-Dateien. Filtert `ERROR`, `WARNING`, `CRITICAL` Eintraege. Pagination und Zeitraum-Filter.

### 7.12 Webhook-Integration (F60)

**Route:** `web/routes/webhook_route.py`

GitHub Webhook Endpoint fuer Auto-Deployment. Verifiziert HMAC-Signatur. Kann automatisch `git pull` + Service-Restart ausloesen.

### 7.13 Dark Mode (F46)

**Datei:** `web/static/themes.css` + `web/templates/base.html`

Client-seitiger Theme-Toggle (Light/Dark). Speichert Praeferenz in Cookie. CSS Custom Properties fuer konsistente Farben.

### 7.14 Health-API (F34)

**Route:** `web/routes/health_route.py`

Public Endpoint (keine Authentifizierung noetig):
- `GET /api/health` — Server-Status aller Gameserver + Bot-Status
- `GET /api/health/selftest` — Selftest-Ergebnisse (F62)
- `GET /api/health/auto-restart` — Health Auto-Restart Status (F27)
- `GET /api/health/disk` — Disk Guard Status (F49)
- `GET /api/health/services` — Service Watchdog Status (F50)
- `GET /api/health/dns` — DuckDNS Monitor Status (F51)
- `GET /api/health/ports` — Port Monitor Status (F52)

---

## 8. Datenbank (SQLite)

### 8.1 Migration von JSON (F28)

**Modul:** `modules/database/db_manager.py`

Zentrale SQLite-Datenbank (`data/botdata.db`) ersetzt alle JSON-Datenspeicher. Vorteile:
- Keine Race Conditions mehr (WAL-Modus)
- ACID-Transaktionen
- Effiziente Abfragen (Indizes, FTS5)
- Concurrent Access von mehreren Bots

**Connection-Management:** Singleton-Pattern via `get_db()`. WAL-Modus und Foreign Keys werden bei jedem Connect aktiviert.

### 8.2 Schema & Tabellen

31 Tabellen in Schema-Version 1:

| Tabelle | Beschreibung | Retention |
|---------|-------------|-----------|
| stats_history | System-Metriken (CPU, RAM, Disk) | 90 Tage |
| events | System-Events und Benachrichtigungen | 30 Tage |
| audit_log | Dashboard-Aktionen | 365 Tage |
| command_log | Bot-Command-Ausfuehrungen | 90 Tage |
| player_sessions | Spieler-Sessions (Join/Leave) | Unbegrenzt |
| players | Spieler-Stammdaten | Unbegrenzt |
| backup_history | Backup-Protokoll | Unbegrenzt |
| alerts_sent | Gesendete Benachrichtigungen (F54) | 30 Tage (resolved) |
| config_history | Config-Aenderungen (F53) | Unbegrenzt |
| custom_commands | Custom Commands (F43) | Unbegrenzt |
| search_index | FTS5 Volltextindex (F55) | Bei Reindex erneuert |
| ... | Weitere System-/Feature-Tabellen | Variabel |

### 8.3 FTS5 Volltextsuche (F55)

**Modul:** `modules/database/search_indexer.py`

SQLite FTS5 Virtual Table fuer Volltextsuche ueber:
- Events
- Player Sessions
- Audit Log
- Command Log
- Backup History
- Custom Commands

**Operationen:**
- `full_reindex()` — Kompletter Neuaufbau
- `incremental_update()` — Nur neue Eintraege seit letztem Lauf
- `search(query, limit=50)` — FTS5 MATCH mit `snippet()` und Ranking

### 8.4 Backup & Rotation (F63)

**Modul:** `modules/database/maintenance.py`

SQLite-Backup via `sqlite3.backup()` API (sicher im WAL-Modus). Backup in eigenem Thread via `asyncio.to_thread()` (Thread-Safety). Integritaetscheck nach jedem Backup via `PRAGMA integrity_check`.

**Rotation:**
- Max 24 stuendliche Backups
- Max 7 taegliche Backups (aeltestes pro Tag)
- Max 4 woechentliche Backups (aeltestes pro Woche)

**Backup-Verzeichnis:** `data/backups/db/`

### 8.5 Data Retention (F56)

Automatische Datenbereinigung mit konfigurierbaren Fristen:

| Tabelle | Retention | Spalte |
|---------|-----------|--------|
| stats_history | 90 Tage | timestamp |
| events | 30 Tage | timestamp |
| audit_log | 365 Tage | timestamp |
| command_log | 90 Tage | timestamp |
| alerts_sent (resolved) | 30 Tage | resolved_at |
| player_sessions | Unbegrenzt | — |

Retention laeuft als Teil der `full_maintenance()` alle 6 Stunden.

### 8.6 Config-Versionierung (F53)

**Modul:** `modules/config_history.py`

Tracked alle Aenderungen an `config.json` in SQLite. Speichert Diff, Zeitstempel und Quelle. Ermoeglicht Rollback auf fruehere Config-Versionen.

---

## 9. ENV-Variablen Referenz

### 9.1 Discord

Pflicht: `DISCORD_TOKEN_MANAGER`, `DISCORD_TOKEN_WATCHDOG`, `GUILD_ID`, `OWNER_ID`, `ADMIN_ROLE_ID`, `SATISFACTORY_ROLE_ID`, `ADMIN_LOG_CHANNEL_ID`, `PUBLIC_STATUS_CHANNEL_ID`.
Optional: `STATUS_EMBED_CHANNEL_ID`, `VOICE_STATS_CATEGORY_ID`, `NOTIFY_ROLE_ID`, `MINECRAFT_ROLE_ID`.

### 9.2 Satisfactory

Server: `SATISFACTORY_SERVICE`, `SATISFACTORY_USER`, `SATISFACTORY_SERVER_PATH`, `SATISFACTORY_SAVE_PATH`.
API: `API_HOST`, `API_PORT`, `API_TOKEN`, `API_VERIFY_SSL`.
Updates: `STEAMCMD_PATH`.

### 9.3 Minecraft Multi-Server

`MC_{ID}_SERVICE`, `MC_{ID}_DISPLAY_NAME`, `MC_{ID}_PATH`, `MC_{ID}_WORLD_PATH`, `MC_{ID}_RCON_HOST`, `MC_{ID}_RCON_PORT`, `MC_{ID}_RCON_PASSWORD`, `MC_{ID}_BACKUP_PATH`, `MC_{ID}_LOG_PATH`, `MC_{ID}_GAME_CHAT_CHANNEL_ID`.

IDs: BMC, VANILLA.

### 9.4 Backup & Cloud

`BACKUP_PATH`, `ONEDRIVE_ENABLED`, `ONEDRIVE_REMOTE`, `ONEDRIVE_PATH`.

### 9.5 E-Mail (Optional)

`EMAIL_ENABLED`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_FROM`, `EMAIL_TO`.

### 9.6 Web-Status (Optional)

`WEB_STATUS_ENABLED`, `WEB_STATUS_PATH`.

### 9.7 Modpack-Updates (Optional)

`MC_BMC_MODPACK_ID`, `MC_BMC_MODPACK_VERSION`, `MC_BMC_MODPACK_SOURCE`, `CURSEFORGE_API_KEY`.

### 9.8 Admin Bot

`ADMIN_BOT_TOKEN` (Pflicht).

### 9.9 TeamSpeak (Optional)

`TS_ENABLED`, `TS_HOST`, `TS_PORT`, `TS_QUERY_USER`, `TS_QUERY_PASS`, `TS_SERVER_ID`.

### 9.10 Web-Dashboard

`WEB_ENABLED`, `WEB_PORT`, `WEB_SECRET_KEY`, `WEB_ADMIN_USER`, `WEB_ADMIN_PASS_HASH`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `WEB_WEBMIN_URL` (optional).

### 9.11 Monitoring (Neu in v4.0.0)

`DUCKDNS_TOKEN` (Optional — fuer DuckDNS Auto-Update bei IP-Mismatch, F51).
`GITHUB_WEBHOOK_SECRET` (Optional — fuer Webhook-Verifizierung, F60).

---

## 10. Server-Infrastruktur

### 10.1 Hardware

Netcup RS 4000 G12: 12 vCores, 31 GB RAM, 1007 GB NVMe. Ubuntu 22.04.5 LTS. Python 3.10.12. IP: 203.0.113.10:4422.

### 10.2 Dienste und Ports

| Port | Protokoll | Service | Zugriff |
|------|-----------|---------|---------|
| 443 | TCP/HTTPS | Nginx → Web-Dashboard | Extern |
| 8080 | TCP/HTTP | Web-Dashboard (uvicorn) | Nur localhost |
| 4422 | TCP | SSH | Extern |
| 7777 | TCP+UDP | Satisfactory Game | Extern |
| 15777 | UDP | Satisfactory Query (Health-Check) | Extern |
| 25565 | TCP | MC Vanilla | Extern |
| 25566 | TCP | MC Better MC | Extern |
| 25575 | TCP | MC RCON (BMC) | Nur localhost |
| 25576 | TCP | MC RCON (Vanilla) | Nur localhost |

GameServer Bot, Monitor Bot, Admin Bot: Kein eigener Port (Discord-API via Websocket).

### 10.3 RAM-Aufteilung

| Komponente | RAM | Beschreibung |
|-----------|-----|-------------|
| Satisfactory | 14-16 GB | Gameserver (Megabase, 2-4 Spieler) |
| MC Vanilla | 2-4 GB | Paper MC (derzeit offline) |
| MC BMC5 | 4-12 GB | NeoForge 1.21.1, Xms4G/Xmx12G, G1GC |
| Monitor Bot | ~300 MB | Groesster Bot (alle Monitoring-Tasks) |
| GameServer Bot | ~50 MB | Slash Commands |
| Admin Bot | ~50 MB | Moderation, Community |
| Web-Dashboard | ~50 MB | FastAPI + Uvicorn |
| System | 6-8 GB | OS, Nginx, Fail2Ban, etc. |

### 10.4 systemd Services

| Service | User | Restart | MemoryMax | Security-Hardening |
|---------|------|---------|-----------|--------------------|
| monitor-bot.service | botuser | on-failure (15s) | 768M | ProtectSystem, ProtectHome=read-only, PrivateTmp |
| web-dashboard.service | botuser | on-failure (10s) | 512M | ProtectSystem, ProtectHome=read-only, PrivateTmp |
| admin-bot.service | botuser | on-failure | — | ProtectSystem, ProtectHome=read-only |
| gameserver-bot.service | botuser | on-failure | — | ProtectSystem, ProtectHome=read-only |
| satisfactory.service | satisfactory | — | — | — |
| minecraft-vanilla.service | — | — | — | — |
| minecraft-bmc.service | minecraft | — | 14G | NeoForge 1.21.1 via run.sh |

Alle Bot-Services nutzen `ReadWritePaths` fuer `data/`, `logs/`, `config/` und `ProtectControlGroups=true`.

### 10.5 SSH-Zugang

`ssh netcup-marco` (sudo), `ssh netcup-botuser` (SCP). Port 4422. Fail2Ban schuetzt SSH (Jail: sshd + recidive).

### 10.6 Deployment-Workflow

1. Lokale Dateien bearbeiten
2. SCP Upload: `scp -P 4422 <datei> marco@203.0.113.10:/tmp/`
3. Auf Server: `sudo cp /tmp/<datei> /home/botuser/Discord_Bots/<pfad>`
4. Ownership: `sudo chown botuser:botuser <datei>`
5. Service neustarten: `sudo systemctl restart <service>`
6. Logs pruefen: `sudo journalctl -u <service> --since '2 min ago' --no-pager`

---

## 11. Sicherheit

### Bestehende Sicherheitsmassnahmen (seit v3.x)

**RCON-Injection-Schutz:** Alle Nachrichten die via RCON an Minecraft gesendet werden, durchlaufen eine Sanitisierung ueber `_sanitize_rcon_input()` mit Whitelist erlaubter Zeichen. Zusaetzlich werden Minecraft Target-Selektoren (`@a`, `@p`, `@e`, `@r`, `@s`) gefiltert.

**Mention-Injection-Schutz:** Alle Nachrichten die von Minecraft nach Discord weitergeleitet werden, nutzen `AllowedMentions.none()`. Spielernamen und Chat-Nachrichten werden mit `discord.utils.escape_markdown()` und `escape_mentions()` behandelt.

**Path-Traversal-Schutz:** Backup-Restore und -Delete Operationen validieren Pfade mit `.resolve()` und pruefen ob der aufgeloeste Pfad innerhalb des erlaubten Backup-Verzeichnisses liegt.

**Command-Injection-Prevention:** systemctl-Aufrufe nutzen eine `ALLOWED_ACTIONS` Whitelist (frozenset). Alle Subprocess-Aufrufe verwenden `create_subprocess_exec()` statt Shell-Interpolation.

**API-Sicherheit:** Satisfactory API-Kommunikation erfolgt ueber HTTPS mit Bearer-Token. SSL-Verifizierung ist konfigurierbar. Session-Erstellung ist durch `asyncio.Lock` gegen Race Conditions geschuetzt.

**Race-Condition-Schutz:** RCON-Verbindungen nutzen `asyncio.Lock` um parallele Aufrufe zu serialisieren. Datenbank-Zugriffe sind durch SQLite WAL-Modus (seit v4.0.0) thread-safe.

**Async-Sicherheit:** Blockierende Aufrufe wie `psutil.cpu_percent()` und synchrone File-I/O wurden in `asyncio.to_thread()` gewrappt. SQLite-Backups nutzen eigene Connections in `asyncio.to_thread()`.

**UFW/Player-IP-Tracking:** Der Player-IP-Tracker kann IPs von Spielern via UFW blocken. IP-Adressen werden vor Verwendung mit Regex auf gueltiges IPv4-Format geprueft.

**Berechtigungssystem:** Vierstufiges System: Owner (Bot-Besitzer, alle Rechte), Admin (Admin-Rolle, Server-Steuerung), Spieler (Spieler-Rolle, Info + Aktionen), Alle (nur lesende Befehle). Implementiert ueber `admin_only()`, `owner_only()` und `server_online_required()` Decorators. Rollenbasierter `/help`-Befehl zeigt nur Commands an, die der User ausfuehren darf.

**Word Filter & Anti-Spam:** Konfigurierbare Wortfilter-Patterns (partial/exact/regex) und Rate-Limiting (5 Nachrichten/10s, 3 Commands/10s). Im Admin Bot.

**Config-Backup-Verschluesselung:** Optionale GPG AES256-Verschluesselung fuer Config-Backups. Aktiviert durch `GPG_PASSPHRASE` ENV-Variable.

### Neue Sicherheitsmassnahmen in v4.0.0

**CSRF-Schutz (F64):** Token-basierter CSRF-Schutz fuer alle POST/PUT/DELETE/PATCH-Requests im Dashboard. Token wird pro Session generiert und ueber Meta-Tag im HTML sowie Cookie bereitgestellt. Exempt: `/auth/login`, `/auth/discord/callback`, `/api/health`, `/ws`.

**Session-Timeout (F65):** Automatischer Logout nach 60 Minuten Inaktivitaet. Absoluter Timeout nach 24 Stunden. Verlaengerbar auf 7 Tage mit "Remember Me". Public Paths werden nicht getrackt.

**Rate-Limiting (F48):** Token-Bucket-basiertes Rate-Limiting: Login 5/min, Actions (POST/PUT/DELETE) 10/min, Read (GET) 60/min. Verhindert Brute-Force und Denial-of-Service.

**Graceful Shutdown (F61):** Signal-Handler fuer SIGTERM/SIGINT. Geordnetes Herunterfahren mit WAL-Checkpoint, offene Connections schliessen, Background-Tasks stoppen.

**Pre-Boot Selftest (F62):** Automatische Verifikation aller kritischen Komponenten beim Bot-Start. Verhindert Start mit fehlender Konfiguration oder unerreichbaren Services.

**SSL-Monitoring (F32):** Automatische Warnung vor Zertifikats-Ablauf (14 Tage / 3 Tage).

**Fail2Ban-Integration (F31):** Dashboard-Anzeige von Fail2Ban-Status. SSH und Recidive Jails aktiv.

**Webhook-Verifizierung (F60):** HMAC-Signatur-Validierung fuer GitHub Webhooks.

**Datenbank-Sicherheit:** SQLite im WAL-Modus mit Foreign Keys. Backup via dediziertem Thread (Thread-Safety). Parameterisierte Queries (keine SQL-Injection). `.env` Permissions 600.

---

## 12. Entwicklungshistorie

### v1.0.0 — Initiale Version (Januar 2026)

Grundlegendes 2-Bot-System fuer Satisfactory. Basis-Commands (start/stop/status), Health Check, einfache Backups, Dashboard-Embed, Daily Restart.

### v2.0.0 — Feature-Erweiterung (Februar 2026)

Erweiterte Satisfactory-Features: Blueprints mit Kategorien, Whitelist/Blacklist, Chat-Bridge, Savegame-Analyse (Header-Parser + satisfactory-save), SteamCMD-Updates, OneDrive-Backup via rclone, E-Mail-Benachrichtigungen, Player-Tracking mit Wochenberichten, Crash-Replay, Performance-Monitoring, Voice-Channel Stats, Command Audit-Logging, Word Filter und Anti-Spam.

### v2.2.0 — Code-Review + Bugfixes (18. Februar 2026)

Umfassender Code-Review ueber 56 Dateien. 12 kritische Fehler behoben (Shell-Injection, Command-Injection, fehlende Imports, Async-Bugs). 8 Logik-Fehler behoben (Race Conditions, Nested Event Loops, Off-by-one). 52 Code-Qualitaet-Verbesserungen.

### v3.0.0 — Minecraft-Integration (20. Februar 2026)

Komplette Minecraft Multi-Server Integration (18 Commits). 6 neue MC-Module. Neue systemd Services. MC-SAT Feature Parity. Code-Review: 3 Critical, 12 Warning, 1 Bug behoben. Deployment auf Server abgeschlossen.

### v3.1.0 — Sicherheits-Review + Feature-Erweiterung (20. Februar 2026)

Komplett-Review ueber 63 Python-Dateien: 28 CRITICAL Fixes, 8 WARNING Fixes. Neue Features: Server-Offline-Decorator, MC Autosave-Command, Backup-Statistiken, Config-Backup mit GPG-Verschluesselung, MC Blacklist-System, Scheduled Messages, Web-Status-Seite mit Nginx, BMC Modpack-Update-Check, /clear Abbruchfunktion.

### v3.2.0 — Admin Bot + Web-Dashboard + Command-Aufraeumung (20. Februar 2026)

**Phase 10 — Unabhaengige P2 Features:** MC Gameplay-Commands entfernt (F22). MC Ankuendigungs-Banner (F21). MC IP-Ban ueber UFW (F23). Rollenbasierter Help-Befehl (F26). SAT Auto-Update Verbesserung (F20). MC World-Analyse per Command (F11). Timeout-System Erweiterung (F24).

**Phase 11 — Admin Bot (F18):** Komplett neuer dritter Discord-Bot mit 8 Modulen und 10 Cogs. Moderation, Warn-System, Reaction Roles, Leveling, Tickets, Audit, Giveaways.

**Phase 12 — Admin Bot Features:** Temp Voice Channels (F17). TeamSpeak-Integration (F16). Discord + TeamSpeak Server-Backup (F19).

**Phase 13 — Web-Dashboard (F13+F14):** Vollstaendiges Admin-Dashboard mit FastAPI + HTMX + Jinja2. Discord OAuth2 Login. 7 Route-Module, 9 Seiten, 17 Partials, Dark-Theme. Dashboard-Uebersicht, Server-Detail mit RCON-Konsole, Fehler-Uebersicht, Admin Bot Setup, Config-Panel, System-Seite.

**Phase 14 — Command-Aufraeumung (F25):** ~2100 Zeilen Code entfernt. Server-Steuerung ins Dashboard migriert.

### v4.0.0 — Monitoring + SQLite + Dashboard-Erweiterung (22. Februar 2026, aktuell)

Major-Upgrade mit 39 neuen Features (F27-F65) in 5 Phasen.

**Phase 1 — Kern-Infrastruktur (14 Features):**
Graceful Shutdown mit Signal-Handling und WAL-Checkpoint (F61). Pre-Boot Selftest mit 17+ Checks (F62). CSRF-Schutz fuer alle POST/PUT/DELETE Requests (F64). Session-Timeout mit 3-stufiger Ablauflogik (F65). Health Auto-Restart mit UDP/TCP-Probes fuer alle Gameserver (F27). Disk Guard mit 3-Stufen-Warnung (F49). Service Watchdog fuer systemd-Services (F50). DuckDNS Monitor mit Auto-Update (F51). Port Monitor mit TCP-Connect-Tests (F52). Fail2Ban Status-Integration (F31). SSL-Zertifikat-Monitor (F32). Backup-Integritaetspruefung (F33). Public Health-API Endpoint (F34). Token-Bucket Rate Limiter (F48).

**Phase 2 — Datenbank (4 Features):**
Komplette Migration aller JSON-Datenspeicher auf SQLite mit WAL-Modus (F28). Schema-Versionierung mit automatischer Migration. Data-Retention mit konfigurierbaren Fristen pro Tabelle (F56). SQLite-Backup via `sqlite3.backup()` API mit Rotation (F63). Config-Versionierung mit Diff-Tracking (F53).

**Phase 3 — Dashboard-Erweiterungen (9 Features):**
SSE Live-Updates ohne Polling (F29). Korrelations-Dashboard fuer Metrik-Zusammenhaenge (F35). CSV-Export fuer alle Dashboard-Daten (F36). Ressourcen-Forecasting via lineare Regression (F37). Error-Dashboard mit Log-Analyse (F44). FTS5 Volltextsuche ueber alle Daten (F55). Stats Collector alle 5 Minuten (F57). Analytics-Dashboard mit Chart.js (F58). Changelog-Seite aus Markdown (F45).

**Phase 4 — Bot-Features (7 Features):**
Crash Replay mit Log-Kontext-Erfassung (F30). Moderation-Erweiterungen (F39). Leveling/XP-System (F40). Giveaway-System (F41). Custom Commands mit SQLite-Persistenz (F43). Alert-Deduplizierung in SQLite (F54). Graceful Degradation mit auto-Recovery (F59).

**Phase 5 — Feinschliff (5 Features):**
Maintenance-Mode per Discord-Command (F38). Woechentlicher Paket-Update-Check (F42). Client-seitiger Dark/Light Mode (F46). Performance-Optimierungen (F47). GitHub Webhook-Integration mit HMAC-Verifizierung (F60).

**Post-Upgrade Review:**
- 160 Python-Dateien, 0 Syntax-Fehler
- 26/26 Cogs registriert, 20/20 Routes registriert
- 39/39 Features implementiert
- 3 kritische Fehler gefunden und behoben: SAT-Restart-Loop (Config-Schwellenwerte), Dashboard HTTP 500 (Middleware-Reihenfolge), DB-Backup 0 Bytes (Thread-Safety)
- Smoke-Test bestanden, Gesamtbewertung: SEHR GUT

---

## 13. Konfigurationsdateien

**config.json:** Feature-Toggles (24 Flags), Scheduler-Intervalle (33 Eintraege), Schwellwerte fuer Monitoring, Anti-Spam-Regeln, Health-Check-Parameter, Service-Listen.

**.env:** 76 Schluessel in 11 Kategorien. Alle Tokens, API-Keys und sensiblen Daten. Permissions: 600.

**.env.example:** Alle ENV-Variablen als Vorlage mit Beschreibungen.

**requirements.txt:** 16 Abhaengigkeiten (discord.py, FastAPI, aiosqlite, etc.).

---

## 14. Abschluss

Alle Phasen des Projekts sind abgeschlossen (Stand: 12. Maerz 2026, Version 4.0.1):

**Server-Setup:** Satisfactory Dedicated Server laeuft mit HTTPS API. Minecraft BMC5 (NeoForge 1.21.1, ca. 200 Mods) ist aktiv. Minecraft Vanilla (Paper MC) ist eingerichtet aber derzeit offline. Java 21, systemd Services, UFW-Regeln und rcon-cli (v0.10.3, outdead) sind installiert und konfiguriert.

**Discord-Integration:** Drei Discord-Bots sind aktiv: GameServer Bot (6 Cogs) fuer Server-Steuerung, Monitor Bot (4 Cogs, 12+ Monitoring-Module) fuer automatisierte Tasks, Admin Bot (16 Cogs) fuer Moderation und Community-Features. Chat-Bridge Channels fuer beide MC-Server. Scheduled Messages fuer geplante Ankuendigungen.

**Web-Dashboard:** Interaktives Admin-Dashboard mit FastAPI + HTMX + Jinja2. Discord OAuth2 Login mit Guild-/Rollen-Pruefung. 20 Route-Module, 30 Templates. Server-Uebersicht, RCON-Konsole, Fehler-Monitoring, Config-Panel, System-Seite, Analytics, Volltextsuche, Changelog, CSV-Export, SSE Live-Updates. Dark Mode.

**Datenbank:** Zentrale SQLite-Datenbank mit WAL-Modus. 31 Tabellen. FTS5 Volltextsuche. Automatische Backup-Rotation und Data-Retention. Schema-Versionierung mit Migration.

**Monitoring:** 12+ Monitoring-Module: Health Auto-Restart, Service Watchdog, Disk Guard, Port Monitor, DuckDNS Monitor, SSL Monitor, Fail2Ban, Package Checker, Stats Collector, Forecasting, Crash Replay, Graceful Degradation. Alert-Deduplizierung. JSON-Bridge Pattern zum Dashboard.

**Sicherheit:** 5-Layer Middleware-Stack (Session, Timeout, CSRF, Rate-Limiter, CORS). Injection-Schutz (RCON, Mention, Path-Traversal, Command, SQL). Race-Condition-Absicherung (SQLite WAL, asyncio.Lock). Pre-Boot Selftest. Graceful Shutdown. Fail2Ban. SSL-Monitoring. Webhook HMAC-Verifizierung. `.env` Permissions 600.

**Deployment:** Alle 164 Code-Dateien sind auf dem Server deployed. Alle Services als systemd Units mit Security-Hardening. Nginx Reverse-Proxy mit Let's Encrypt SSL.

### Neue Abhaengigkeiten (v4.0.0)

`aiosqlite>=0.19.0` (Async SQLite Driver fuer Datenbank-Migration F28). Alle anderen Dependencies waren bereits in v3.2.0 vorhanden.

---

## Review v4.0.0

Am 22.02.2026 wurde ein vollstaendiger Post-Upgrade-Review durchgefuehrt.

- **Ergebnis:** SEHR GUT
- **Features:** 39/39 vollstaendig implementiert
- **Kritische Fehler:** 3 gefunden und behoben
  - Middleware-Reihenfolge (Dashboard HTTP 500)
  - Health-Checker Schwellenwerte (SAT-Server Restart-Loop)
  - DB-Backup Thread-Safety (0-Byte Backups)
- **Warnungen:** 5 (3 behoben, 2 offen)
- **Smoke-Test:** Bestanden
- **Vollstaendiger Report:** `docs/REVIEW_v4.0.0.md`

## BMC5-Migration (12. Maerz 2026)

Migration von Better MC 3 (Forge) auf Better MC 5 (NeoForge 1.21.1):

- **Server:** NeoForge 21.1.217, BMC5 Server Pack v47, ca. 200 Mods
- **Pfad:** `/home/minecraft/bettermc/` → `/home/minecraft/bmc5/`
- **RAM:** Xms4G / Xmx12G mit G1GC-Tuning, systemd MemoryMax=14G
- **Chat-Bridge:** Regex-Patterns fuer NeoForge-Log-Format erweitert (zusaetzlicher Logger-Tag in eckigen Klammern)
- **systemd:** `minecraft-bmc.service` und `monitor-bot.service` (ReadWritePaths) auf neue Pfade aktualisiert
- **Fixes:** Blueprint-Delete Berechtigungspruefung, Scheduler Rollback Health-Suppress, Chat-Bridge Word-Boundary-Matching
- **Vollstaendiger Bericht:** `docs/SESSION_STAND_BMC5_MIGRATION.md`

---

### Moegliche zukuenftige Erweiterungen

- Satisfactory Chat-Bridge reaktivieren sobald die API stabile Chat-Endpoints bietet
- Multi-Guild Support fuer mehrere Discord-Server
- Satisfactory Webhook-Bridge als Alternative zur API-basierten Chat-Bridge
- Redis-Cache fuer haeufig abgefragte Dashboard-Daten
- Prometheus/Grafana-Integration fuer externes Monitoring
- Mobile-optimiertes Dashboard-Layout
