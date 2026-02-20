# Discord Bot System — Projektdokumentation v3.2.0

> **Version:** 3.2.0 | **Datum:** 20. Februar 2026 | **Autor:** Marco

---

## Inhaltsverzeichnis

1. [Projektuebersicht](#1-projektuebersicht)
   - 1.1 Eckdaten
   - 1.2 Unterstuetzte Gameserver
2. [Architektur](#2-architektur)
   - 2.1 Bot-Aufteilung
   - 2.2 Satisfactory-Architektur
   - 2.3 Minecraft Multi-Server Architektur
   - 2.4 Web-Dashboard Architektur
   - 2.5 Modul-Uebersicht
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
   - 6.1 Health Checks
   - 6.2 Auto-Backup
   - 6.3 Daily Restart
   - 6.4 Player-Tracking
   - 6.5 Update-Checks
   - 6.6 Crash Replay
   - 6.7 Selftest
   - 6.8 Weitere Monitoring-Features
   - 6.9 Web-Status-Seite
   - 6.10 Scheduled Messages
   - 6.11 Statistics Collector
7. [ENV-Variablen Referenz](#7-env-variablen-referenz)
   - 7.1 Discord
   - 7.2 Satisfactory
   - 7.3 Minecraft Multi-Server
   - 7.4 Backup & Cloud
   - 7.5 E-Mail
   - 7.6 Web-Status
   - 7.7 Modpack-Updates
   - 7.8 Admin Bot
   - 7.9 TeamSpeak
   - 7.10 Web-Dashboard
8. [Server-Infrastruktur](#8-server-infrastruktur)
   - 8.1 Hardware
   - 8.2 Dienste und Ports
   - 8.3 RAM-Aufteilung
   - 8.4 systemd Services
   - 8.5 SSH-Zugang
   - 8.6 Deployment-Workflow
9. [Sicherheit](#9-sicherheit)
10. [Entwicklungshistorie](#10-entwicklungshistorie)
11. [Konfigurationsdateien](#11-konfigurationsdateien)
12. [Abschluss](#12-abschluss)

---

## 1. Projektuebersicht

Das Discord Bot System ist ein Drei-Bot-System zur Verwaltung von Gameservern ueber Discord. Es steuert einen Satisfactory Dedicated Server sowie zwei Minecraft-Server (Vanilla/Paper + Better MC) auf einem dedizierten Linux-Server. Zusaetzlich bietet ein interaktives Web-Dashboard mit Admin-Oberflaeche zur Serververwaltung, und der Admin Bot uebernimmt Discord-Moderation, Community-Features und TeamSpeak-Integration.

### 1.1 Eckdaten

- 80+ Python-Dateien, ca. 30.000+ Zeilen Code
- 3 Discord-Bots, 20 Cogs, 60+ Module, 5 Utils
- ca. 100+ Slash Commands
- Web-Dashboard mit FastAPI + HTMX + Jinja2
- Python 3.9+ mit discord.py 2.x
- Vollstaendig async (asyncio)
- systemd-Integration fuer alle Dienste

### 1.2 Unterstuetzte Gameserver

Der GameServer Bot (Bot 1) steuert alle Server via Slash Commands. Der Monitor Bot (Bot 2) uebernimmt automatisierte Background-Tasks wie Health Checks, Backups und Chat-Bridges. Der Admin Bot (Bot 3) haelt Moderation, Community-Features, TeamSpeak-Verwaltung und Temp Voice Channels vor.

**Satisfactory:** Dedicated Server mit Steuerung ueber die offizielle HTTPS API (Port 7777). Bietet Savegame-Analyse mit Binary-Header-Parsing, Blueprint-Management mit Kategorien, Whitelist/Blacklist-System, Settings-Backup via API, und automatische Updates via SteamCMD.

**Minecraft Vanilla/Paper:** Paper MC 1.21.4 Build 209. Steuerung ueber systemd + RCON (Port 25576 lokal). Bidirektionale Chat-Bridge via Log-Polling und RCON. Paper API Update-Check. World-Analyse mit NBT-Parsing.

**Minecraft Better MC:** BMC3 Fabric Modpack. Steuerung ueber systemd + RCON (Port 25575 lokal). Bidirektionale Chat-Bridge. Automatischer Modpack-Update-Check via Modrinth/CurseForge API (alle 12 Stunden). IP-Ban via UFW-Integration.

---

## 2. Architektur

### 2.1 Bot-Aufteilung

**GameServer Bot (Bot 1):** Verarbeitet alle Slash Commands der Benutzer. Startet, stoppt und verwaltet Server. Fuehrt RCON-Befehle aus. Verwaltet Backups, Whitelist, Blacklist und Blueprints. Cooldown-Management verhindert Spam bei kritischen Commands.

**Monitor Bot (Bot 2):** Fuehrt automatisierte Background-Tasks aus. Health Checks alle 2 Minuten. Performance-Monitoring alle 5 Minuten. Dashboard-Embed alle 10 Minuten. Auto-Backups alle 6 Stunden. Daily Restart um 04:00. Chat-Bridge fuer Minecraft. Player-Tracking. Update-Checks. Crash-Detection mit Auto-Restart. Statistics Collector.

**Admin Bot (Bot 3):** Haelt Discord-Moderation, Community-Features und TeamSpeak-Steuerung vor. Cogs: moderation, warn, reaction_roles, leveling, tickets, audit, giveaway, temp_voice, teamspeak, server_backup. Token: ADMIN_BOT_TOKEN. Intents: Members, Message Content, Reactions.

### 2.2 Satisfactory-Architektur

Der Satisfactory-Server wird ueber HTTPS API (Port 7777) und systemd gesteuert. Die API-Kommunikation laeuft ueber aiohttp mit konfigurierbarer SSL-Verifizierung.

Module: server.py, api_client.py, whitelist.py, blacklist.py, blueprint_manager.py, savegame_stats.py, savegame_analyzer.py, settings_backup.py, save_header.py.

### 2.3 Minecraft Multi-Server Architektur

Jeder MC-Server wird ueber MC_{SERVER_ID}_* ENV-Variablen konfiguriert. Server-IDs: BMC (Better MC) und VANILLA.

Pro Server Instanzen: Server-Steuerung, Backup, Chat-Bridge, Player-Tracking, Crash-Replay, Stats-Tracking.

### 2.4 Web-Dashboard Architektur

FastAPI + HTMX + Jinja2 Admin-Oberflaeche. Authentication: Discord OAuth2 oder bcrypt Fallback-Login. Port 8080. 7 Route-Module, 9 Seiten, 17 Partials. Dark-Theme. Statistics Collector als Hintergrund-Task.

### 2.5 Modul-Uebersicht

**modules/satisfactory/ (9)**, **modules/minecraft/ (9)**, **modules/monitoring/ (16)**, **modules/backup/ (3)**, **modules/notifications/ (2)**, **modules/teamspeak/ (4)**, **Standalone (7)**, **utils/ (5)**, **web/ (Dashboard)**.

---

## 3. Satisfactory — Detailbeschreibung

### 3.1 Server-Steuerung (systemd)

Klasse SatisfactoryServer. systemctl-Aufrufe nutzen ALLOWED_ACTIONS Whitelist. Server lauft unter Linux-User "satisfactory". Operationen: is_running(), start(), stop(), restart(), get_status().

### 3.2 HTTPS API-Client

Klasse SatisfactoryAPI. POST-basiert, Port 7777. Bearer-Token Authentifizierung. Daten in Dataclasses (ServerState, HealthInfo). Funktionen: query_server_state(), get_server_options(), get_advanced_game_settings(), save_game(), load_game(), set_admin_password(), kick_player(), get_player_list().

### 3.3 Savegame-System

Drei Module: save_header.py (Binary Parser), savegame_stats.py (Auflistung), savegame_analyzer.py (Tiefenanalyse via satisfactory-save Package).

### 3.4 Blueprint-Management

Verwaltet Blueprints im /home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames/blueprints/ Verzeichnis. Upload via ZIP oder 2 Einzeldateien. 6 Kategorien. Metadaten in JSON-DB. Operationen: Upload, Download, List, Delete.

### 3.5 Whitelist & Blacklist

Zwei Module (WhitelistManager, BlacklistManager). JSON-Dateien. async Load/Save, Spieler hinzufuegen/entfernen, Liste anzeigen, Toggle.

### 3.6 Settings-Backup

Klasse SettingsBackup. Sichert Server-Einstellungen via API. Backups mit Zeitstempel. Restore sendet gesicherte Settings zurueck.

### 3.7 Update-Mechanismus (SteamCMD)

UpdateChecker prueft alle 6 Stunden via SteamCMD Build-ID. Benachrichtigung bei neuer Version. Manual Update via /sat update Command. SteamChangelog fuer Changelog-Abrufe. Automatische Updates nicht enabled.

---

## 4. Minecraft — Detailbeschreibung

### 4.1 Multi-Server Architektur

Prefix-basiertes ENV-System (MC_{SERVER_ID}_*). Pro Server automatische Instanzen bei Bot-Start.

### 4.2 RCON-Client

Async RCON-Client mit signed 32-bit Integers, Reconnect, asyncio.Lock. Context Manager: async with MinecraftRCON(...) as rcon.

### 4.3 Chat-Bridge

Bidirektionale Bridge mit Log-Polling alle 5 Sekunden. Regex-basierte Erkennung: Chat, Join/Leave, Advancements, Deaths. Mention-Injection-Schutz via AllowedMentions.none(). RCON-Injection-Schutz.

### 4.4 Backup-System

World-Backup-Manager. async I/O, Path-Traversal-Schutz. Vor Backup: save-all via RCON.

### 4.5 Update-Checker (Paper API)

MinecraftUpdateChecker vergleicht Paper-Build via Paper API. Nur Vanilla/Paper, nicht BMC.

### 4.6 Blacklist-System

Klasse MinecraftBlacklist. Serveruebergreifend. Auto-Durchsetzung via RCON. Ban-Historie mit Grund, Zeitstempel, Admin. JSON-Persistenz. Commands: /mc blacklist add/remove/list/history.

### 4.7 Modpack-Update-Check

ModpackUpdater prueft alle 12 Stunden. Modrinth (bevorzugt) + CurseForge (Fallback). Benachrichtigung bei neuer Version. Manual Check via /mc config modpack_check.

### 4.8 World-Analyse

world_analyzer.py nutzt nbtlib + anvil-parser2. NBT-Parsing. Statistiken: Welt-Groesse, Chunk-Anzahl, Spawn-Punkt, Difficulty. Command: /mc world stats [server]. asyncio.to_thread() fuer non-blocking.

### 4.9 IP-Ban System (UFW)

player_ip_tracker.py kann IPs via UFW blocken. IPv4-Regex-Validierung. Commands: /mc ipban add/remove <ip>.

### 4.10 Ankuendigungs-Banner

Erweiterter /mc say Command mit Banner-Support. Syntax: /mc say "<nachricht>" [server] [banner_type] [repeat]. Banner-Typen: title, subtitle, actionbar. Optional mit Wiederholung.

---

## 5. Slash Commands — Vollstaendige Referenz

### 5.1 Satisfactory-Commands (GameServer Bot)

**Status:** /sat status (Alle)
**Backup:** /sat backup (Admin), /sat backups list (Spieler), /sat restore (Owner), /sat download (Spieler)
**Stats:** /sat stats (Spieler)
**Settings:** /sat settings (Spieler), /sat playerlimit (Admin), /sat autosave (Admin)
**Advanced:** /sat console (Owner), /sat load (Owner), /sat update (Owner)
**Blueprints:** /sat blueprints upload/list/download/delete (Admin)
**Players:** /sat players (Spieler), /sat kick/ban/unban (Admin)
**Whitelist/Blacklist:** /whitelist add/remove/list, /blacklist add/remove/list (Admin)

Removed in v3.2.0: /sat start, /sat stop, /sat restart, /sat cancel, Konfiguration-Commands.

### 5.2 Minecraft-Commands (GameServer Bot)

**Status:** /mc status [server] (Alle)
**Players:** /mc players list/kick/ban [server] (Admin)
**Whitelist:** /mc whitelist add/remove/list [server] (Admin)
**Blacklist:** /mc blacklist add/remove/list/history (Admin)
**Backup:** /mc backup create/list/restore (Spieler/Owner)
**Admin:** /mc command <cmd> (Owner), /mc say (Admin), /mc world stats (Spieler)

Removed in v3.2.0: /mc start, /mc stop, /mc restart, /mc cancel, In-Game-Commands (difficulty, weather, time, gamemode), Konfiguration.

### 5.3 Allgemeine Commands (GameServer Bot)

/help (Alle, rollenbasiert F26), /reload <cog> (Owner), /clear (Admin), /timeout (Admin), /schedule add/list/cancel (Admin).

### 5.4 Monitor Bot Commands

/performance, /dashboard, /stats, /report, /mcstats, /mcreport, /mccrashlog, /scheduler, /update check, /email test|status, /onedrive status|upload|list, /backup stats.

### 5.5 Admin Bot Commands

**Moderation:** /mod warn/warnlist/unwarn/mute/unmute/kick/ban/wordfilter
**Reaction Roles:** /reactionrole add/remove/list
**Leveling:** /level, /leaderboard, /levelconfig
**Tickets:** /ticket create/close/setup
**Giveaway:** /giveaway start/end/reroll
**Temp Voice:** /tempvoice setup/config
**TeamSpeak:** /ts status/users/channels/message
**Server-Backup:** /serverbackup create/restore/list

---

## 6. Monitoring & Automatisierung

### 6.1 Health Checks

SAT alle 2 Min: API + psutil. Nach 3 Fehler: Downtime-Benachrichtigung. Crash-Auto-Restart (max 5/h, 30s Warten).
MC alle 2 Min: systemd + RCON. Nach 3 Fehler: Downtime-Benachrichtigung.

### 6.2 Auto-Backup

SAT: Alle 6h Savegame-Backup. Lokal + OneDrive (verpflichtend).
MC: Alle 6h World-Backup. save-all vor Backup. Lokal + OneDrive.

### 6.3 Daily Restart

04:00 Uhr fuer alle Server. Nur wenn >12h runtime. Skip wenn Spieler online.

### 6.4 Player-Tracking

Pro Server Tracker. Join/Leave Events. Spielzeit. Wochenberichte. JSON-Persistenz.

### 6.5 Update-Checks

SAT: 6h via SteamCMD.
MC Vanilla: 6h via Paper API.

### 6.6 Crash Replay

Letzte 50 Log-Zeilen bei Crash. Analyse + Admin-Channel Zusammenfassung.

### 6.7 Selftest

17 Checks: Discord, SAT API, SAT Prozess, UFW, Disk, Savegame, OneDrive, E-Mail, SteamCMD, Config. Pro MC-Server: Status, RCON, Log-Pfad, Backup-Pfad.

### 6.8 Weitere Monitoring-Features

Performance (5min), Dashboard-Embed (10min), Voice-Stats (5min), Login Audit, Auto-Cleanup, Savegame Protection, Graceful Degradation, Server Optimizer.

### 6.9 Web-Status-Seite

WebStatus Module. HTML-Ausgabe alle 60s. Jinja2 Template. Dark-Mode. Status, Spieler, Performance, Update. Disabled by default (WEB_STATUS_ENABLED=true).

### 6.10 Scheduled Messages

SchedulerCog. Relative/absolute Zeitangaben. Wiederholungen: einmalig, taeglich, woechentlich. Max 20 aktiv. Persistiert.

### 6.11 Statistics Collector

stats_collector.py. Alle 5 Min. Ringbuffer 30 Tage (432 Eintraege). Metriken: CPU, RAM, Disk, Netzwerk, Spieler, Uptime, Ping. Web-Dashboard Nutzung.

---

## 7. ENV-Variablen Referenz

### 7.1 Discord

Pflicht: DISCORD_TOKEN_MANAGER, DISCORD_TOKEN_WATCHDOG, GUILD_ID, OWNER_ID, ADMIN_ROLE_ID, SATISFACTORY_ROLE_ID, ADMIN_LOG_CHANNEL_ID, PUBLIC_STATUS_CHANNEL_ID.
Optional: STATUS_EMBED_CHANNEL_ID, VOICE_STATS_CATEGORY_ID, NOTIFY_ROLE_ID, MINECRAFT_ROLE_ID.

### 7.2 Satisfactory

Server: SATISFACTORY_SERVICE, SATISFACTORY_USER, SATISFACTORY_SERVER_PATH, SATISFACTORY_SAVE_PATH.
API: API_HOST, API_PORT, API_TOKEN, API_VERIFY_SSL.
Updates: STEAMCMD_PATH.

### 7.3 Minecraft Multi-Server

MC_{ID}_SERVICE, MC_{ID}_DISPLAY_NAME, MC_{ID}_PATH, MC_{ID}_WORLD_PATH, MC_{ID}_RCON_HOST, MC_{ID}_RCON_PORT, MC_{ID}_RCON_PASSWORD, MC_{ID}_BACKUP_PATH, MC_{ID}_LOG_PATH, MC_{ID}_GAME_CHAT_CHANNEL_ID.

IDs: BMC, VANILLA.

### 7.4 Backup & Cloud

BACKUP_PATH, ONEDRIVE_ENABLED, ONEDRIVE_REMOTE, ONEDRIVE_PATH.

### 7.5 E-Mail (Optional)

EMAIL_ENABLED, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO.

### 7.6 Web-Status (Optional)

WEB_STATUS_ENABLED, WEB_STATUS_PATH.

### 7.7 Modpack-Updates (Optional)

MC_BMC_MODPACK_ID, MC_BMC_MODPACK_VERSION, MC_BMC_MODPACK_SOURCE, CURSEFORGE_API_KEY.

### 7.8 Admin Bot (Neu)

ADMIN_BOT_TOKEN (Pflicht).

### 7.9 TeamSpeak (Optional)

TS_ENABLED, TS_HOST, TS_PORT, TS_QUERY_USER, TS_QUERY_PASS, TS_SERVER_ID.

### 7.10 Web-Dashboard (Neu)

WEB_ENABLED, WEB_PORT, WEB_SECRET_KEY, WEB_ADMIN_USER, WEB_ADMIN_PASS_HASH, DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI, WEB_WEBMIN_URL (optional).

---

## 8. Server-Infrastruktur

### 8.1 Hardware

Netcup RS 4000 G12: 12 vCores, 32 GB RAM, 950 GB NVMe. Ubuntu 22.04 LTS. IP: 203.0.113.10:4422.

### 8.2 Dienste und Ports

GameServer Bot, Monitor Bot, Admin Bot (kein Port).
Web-Dashboard: 8080.
Satisfactory: 7777, 15000, 15777.
MC Vanilla: 25565 (Game), 25576 (RCON).
MC BMC: 25566 (Game), 25575 (RCON).

### 8.3 RAM-Aufteilung

SAT: 8-12 GB. MC Vanilla: 2-4 GB. MC BMC: 4-8 GB. Bots: 600-1000 MB. Web-Dashboard: 100-200 MB. System: 6-8 GB.

### 8.4 systemd Services

satisfactory.service, minecraft-vanilla.service, minecraft-bmc.service, gameserver-bot.service, monitor-bot.service, admin-bot.service, web-dashboard.service.

### 8.5 SSH-Zugang

ssh netcup-marco (sudo), ssh netcup-botuser (SCP). Port 4422.

### 8.6 Deployment-Workflow

SCP Upload, pip install -r requirements.txt, systemctl restart, journalctl pruefen.

---

## 9. Sicherheit

In Phase 7 (v3.1.0) wurde ein Komplett-Review ueber alle 63 Python-Dateien durchgefuehrt. 28 CRITICAL-Befunde und 8 WARNING-Befunde wurden behoben. In v3.2.0 wurden weitere Sicherheitsmassnahmen fuer das Web-Dashboard eingefuehrt.

**RCON-Injection-Schutz:** Alle Nachrichten die via RCON an Minecraft gesendet werden, durchlaufen eine Sanitisierung ueber `_sanitize_rcon_input()` mit Whitelist erlaubter Zeichen. Zusaetzlich werden Minecraft Target-Selektoren (`@a`, `@p`, `@e`, `@r`, `@s`) gefiltert.

**Mention-Injection-Schutz:** Alle Nachrichten die von Minecraft nach Discord weitergeleitet werden, nutzen `AllowedMentions.none()`. Spielernamen und Chat-Nachrichten werden mit `discord.utils.escape_markdown()` und `escape_mentions()` behandelt.

**Path-Traversal-Schutz:** Backup-Restore und -Delete Operationen validieren Pfade mit `.resolve()` und pruefen ob der aufgeloeste Pfad innerhalb des erlaubten Backup-Verzeichnisses liegt.

**Command-Injection-Prevention:** systemctl-Aufrufe nutzen eine `ALLOWED_ACTIONS` Whitelist (frozenset). Alle Subprocess-Aufrufe verwenden `create_subprocess_exec()` statt Shell-Interpolation.

**API-Sicherheit:** Satisfactory API-Kommunikation erfolgt ueber HTTPS mit Bearer-Token. SSL-Verifizierung ist konfigurierbar. Session-Erstellung ist durch `asyncio.Lock` gegen Race Conditions geschuetzt.

**Race-Condition-Schutz:** RCON-Verbindungen nutzen `asyncio.Lock` um parallele Aufrufe zu serialisieren. Whitelist und Blacklist JSON-Dateien sind durch Locks gegen gleichzeitige Schreibzugriffe geschuetzt.

**Async-Sicherheit:** Blockierende Aufrufe wie `psutil.cpu_percent()` und synchrone File-I/O wurden in `asyncio.to_thread()` gewrappt.

**UFW/Player-IP-Tracking:** Der Player-IP-Tracker kann IPs von Spielern via UFW blocken. IP-Adressen werden vor Verwendung mit Regex auf gueltiges IPv4-Format geprueft.

**Berechtigungssystem:** Vierstufiges System: Owner (Bot-Besitzer, alle Rechte), Admin (Admin-Rolle, Server-Steuerung), Spieler (Spieler-Rolle, Info + Aktionen), Alle (nur lesende Befehle). Implementiert ueber `admin_only()`, `owner_only()` und `server_online_required()` Decorators. Ab v3.2.0: Rollenbasierter `/help`-Befehl zeigt nur Commands an, die der User ausfuehren darf.

**Word Filter & Anti-Spam:** Konfigurierbare Wortfilter-Patterns (partial/exact/regex) und Rate-Limiting (5 Nachrichten/10s, 3 Commands/10s). Ab v3.2.0 im Admin Bot migriert.

**Config-Backup-Verschluesselung:** Optionale GPG AES256-Verschluesselung fuer Config-Backups. Aktiviert durch `GPG_PASSPHRASE` ENV-Variable.

**Web-Dashboard Security (v3.2.0):** Discord OAuth2 Login mit Guild-/Rollen-Pruefung. bcrypt Passwort-Fallback fuer Admin-Login. JWT-basierte Sessions mit httpOnly Cookies (24h Ablaufzeit). XSS-Schutz via `html.escape()` fuer alle User-Inputs. CSRF-Schutz via SameSite=lax Cookies und OAuth2 State-Token. Rate-Limiting: Max 5 Login-Versuche pro 15 Minuten. Exception-Leak-Prevention: Interne Fehlermeldungen nur im Server-Log.

---

## 10. Entwicklungshistorie

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

### v3.2.0 — Admin Bot + Web-Dashboard + Command-Aufraeumung (20. Februar 2026, aktuell)

**Phase 10 — Unabhaengige P2 Features:** MC Gameplay-Commands entfernt (F22, nur In-Game). MC Ankuendigungs-Banner mit Title/Subtitle/Actionbar und Repeat-Modus (F21). MC IP-Ban ueber UFW wie SAT (F23). Rollenbasierter Help-Befehl (F26). SAT Auto-Update Verbesserung mit sofortigem Update bei leerem Server und Auto-Rollback (F20). MC World-Analyse per Command mit nbtlib (F11). Timeout-System Erweiterung mit temporaerem Server-Ban und Restzeit-Anzeige (F24).

**Phase 11 — Admin Bot (F18):** Komplett neuer dritter Discord-Bot mit 8 Modulen und 10 Cogs. Features: Moderation (WordFilter + AntiSpam Migration), Warn-System mit Eskalationsstufen, Reaction Roles, Leveling/XP-System, Ticket-System, Audit-Logging, Giveaway-System.

**Phase 12 — Admin Bot Features:** Temp Voice Channels mit automatischer Erstellung und Loeschung (F17). TeamSpeak-Integration mit ServerQuery-Client, Chat-Bridge und Channel-Management in 3 Phasen (F16). Discord + TeamSpeak Server-Backup mit Struktur-Snapshot (F19).

**Phase 13 — Web-Dashboard (F13+F14):** Vollstaendiges Admin-Dashboard mit FastAPI + HTMX + Jinja2. Discord OAuth2 Login. 7 Route-Module, 9 Seiten, 17 Partials, Dark-Theme. Dashboard-Uebersicht, Server-Detail mit RCON-Konsole, Fehler-Uebersicht, Admin Bot Setup, Config-Panel, System-Seite mit Webmin-Einbettung. Stats Collector als Hintergrund-Task.

**Phase 14 — Command-Aufraeumung (F25):** ~2100 Zeilen Code entfernt. Server-Steuerung (start/stop/restart) ins Dashboard migriert. Admin-Config-Commands entfernt. Mod-Admin-Commands entfernt. Nur Lese-Commands bleiben in Discord.

---

## 11. Konfigurationsdateien

**config.json:** Feature-Toggles, Intervalle, Schwellwerte.
**.env.example:** Alle ENV-Variablen in 11 Kategorien.

---

## 12. Abschluss

Alle Phasen des Projekts sind abgeschlossen (Stand: 20. Februar 2026, Version 3.2.0):

**Server-Setup:** Satisfactory Dedicated Server laeuft mit HTTPS API. Beide Minecraft-Server (Vanilla/Paper + BMC3 Fabric) sind eingerichtet. Java 21, systemd Services, UFW-Regeln und rcon-cli sind installiert und konfiguriert.

**Discord-Integration:** Drei Discord-Bots sind aktiv: GameServer Bot fuer Server-Steuerung, Monitor Bot fuer automatisierte Tasks, Admin Bot fuer Moderation und Community-Features. Chat-Bridge Channels fuer beide MC-Server. Scheduled Messages fuer geplante Ankuendigungen.

**Web-Dashboard:** Interaktives Admin-Dashboard mit FastAPI + HTMX + Jinja2. Discord OAuth2 Login mit Guild-/Rollen-Pruefung. Server-Uebersicht, RCON-Konsole, Fehler-Monitoring, Config-Panel und System-Seite mit Webmin-Einbettung.

**Sicherheit:** Komplett-Review ueber 63+ Dateien mit 28 CRITICAL Fixes. Injection-Schutz, Race-Condition-Absicherung und async-sichere I/O in allen Modulen. Web-Dashboard mit OAuth2, JWT, CSRF-Schutz und Rate-Limiting.

**Monitoring:** Web-Status-Seite (optional) fuer externen Zugriff. Stats Collector mit Ringbuffer (30 Tage). BMC Modpack-Update-Check. Backup-Statistiken mit Disk-Usage. Config-Backup mit optionaler GPG-Verschluesselung.

**Deployment:** Alle Code-Dateien sind auf dem Server deployed. Alle drei Bots und das Web-Dashboard sind als systemd Services eingerichtet.

### Neue Abhaengigkeiten (v3.2.0)

`fastapi` und `uvicorn` (Web-Dashboard Server), `python-jose` (JWT-Token fuer Web-Sessions), `bcrypt` (Passwort-Hashing fuer Fallback-Login), `aiohttp` (TeamSpeak ServerQuery Client), `nbtlib` und `anvil-parser2` (MC World-Analyse NBT-Parsing), `jinja2` (bereits ab v3.1.0 fuer Web-Status-Template).

### Moegliche zukuenftige Erweiterungen

Satisfactory Chat-Bridge reaktivieren sobald die API stabile Chat-Endpoints bietet. Datenbank-Migration von JSON zu SQLite fuer hoehere Performance bei grossen Datenmengen. Multi-Guild Support fuer mehrere Discord-Server — erst relevant wenn tatsaechlich benoetigt. Satisfactory Webhook-Bridge als Alternative zur API-basierten Chat-Bridge.
