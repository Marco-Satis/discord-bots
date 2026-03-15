# Discord Bot System — Projektdokumentation v3.1.0

> **Version:** 3.1.0 | **Datum:** 20. Februar 2026 | **Autor:** Marco

---

## Inhaltsverzeichnis

1. [Projektuebersicht](#1-projektuebersicht)
   - 1.1 Eckdaten
   - 1.2 Unterstuetzte Gameserver
2. [Architektur](#2-architektur)
   - 2.1 Bot-Aufteilung
   - 2.2 Satisfactory-Architektur
   - 2.3 Minecraft Multi-Server Architektur
   - 2.4 Modul-Uebersicht
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
5. [Slash Commands — Vollstaendige Referenz](#5-slash-commands--vollstaendige-referenz)
   - 5.1 Satisfactory-Commands (GameServer Bot)
   - 5.2 Minecraft-Commands (GameServer Bot)
   - 5.3 Allgemeine Commands (GameServer Bot)
   - 5.4 Monitor Bot Commands
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
7. [ENV-Variablen Referenz](#7-env-variablen-referenz)
   - 7.1 Discord
   - 7.2 Satisfactory
   - 7.3 Minecraft Multi-Server
   - 7.4 Backup & Cloud
   - 7.5 E-Mail
   - 7.6 Web-Status
   - 7.7 Modpack-Updates
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

Das Discord Bot System ist ein Zwei-Bot-System zur Verwaltung von Gameservern ueber Discord. Es steuert einen Satisfactory Dedicated Server sowie zwei Minecraft-Server (Vanilla/Paper + Better MC) auf einem dedizierten Linux-Server.

### 1.1 Eckdaten

- 66 Python-Dateien, ca. 23.400 Zeilen Code
- 2 Discord-Bots, 9 Cogs, 50 Module, 5 Utils
- ca. 80 Slash Commands
- Python 3.9+ mit discord.py 2.x
- Vollstaendig async (asyncio)
- systemd-Integration fuer alle Dienste

### 1.2 Unterstuetzte Gameserver

Der GameServer Bot (Bot 1) steuert alle Server via Slash Commands. Der Monitor Bot (Bot 2) uebernimmt automatisierte Background-Tasks wie Health Checks, Backups und Chat-Bridges.

**Satisfactory:** Dedicated Server mit Steuerung ueber die offizielle HTTPS API (Port 7777). Bietet Savegame-Analyse mit Binary-Header-Parsing, Blueprint-Management mit Kategorien, Whitelist/Blacklist-System, Settings-Backup via API, und automatische Updates via SteamCMD.

**Minecraft Vanilla/Paper:** Paper MC 1.21.4 Build 209. Steuerung ueber systemd + RCON (Port 25576 lokal). Bidirektionale Chat-Bridge via Log-Polling und RCON. Paper API Update-Check.

**Minecraft Better MC:** BMC3 Fabric Modpack. Steuerung ueber systemd + RCON (Port 25575 lokal). Bidirektionale Chat-Bridge. Automatischer Modpack-Update-Check via Modrinth/CurseForge API (alle 12 Stunden).

---

## 2. Architektur

### 2.1 Bot-Aufteilung

**GameServer Bot (Bot 1):** Verarbeitet alle Slash Commands der Benutzer. Startet, stoppt und verwaltet Server. Fuehrt RCON-Befehle aus. Verwaltet Backups, Whitelist, Blacklist und Blueprints. Laedt beim Start folgende Shared Instances: SatisfactoryServer, SatisfactoryAPI, WhitelistManager, BlacklistManager, BlueprintManager, SavegameStats, BackupManager, RestartTimerManager, WordFilter, AntiSpam, CommandLogger, UpdateChecker, SettingsBackup. Cooldown-Management verhindert Spam bei kritischen Commands (start: 30s, restart: 60s, stop: 30s).

**Monitor Bot (Bot 2):** Fuehrt automatisierte Background-Tasks aus. Health Checks alle 2 Minuten (SAT + MC). Performance-Monitoring alle 5 Minuten. Dashboard-Embed alle 10 Minuten. Auto-Backups alle 6 Stunden. Daily Restart um 04:00. Chat-Bridge fuer Minecraft (Log-Polling alle 5 Sekunden). Player-Tracking (Join/Leave/Spielzeit). Update-Checks (SteamCMD + Paper API). Crash-Detection mit Auto-Restart.

### 2.2 Satisfactory-Architektur

Der Satisfactory-Server wird ueber zwei Schnittstellen gesteuert: Die offizielle HTTPS API (POST-basiert, Port 7777) fuer Spieler-Management, Server-Status, Einstellungen und Savegame-Operationen. Und systemd fuer Prozess-Steuerung (start/stop/restart/status). Die API-Kommunikation laeuft ueber aiohttp mit konfigurierbarer SSL-Verifizierung (Self-Signed Zertifikate).

Die SAT-Module sind in 9 Dateien organisiert: server.py (Prozess-Steuerung via systemd, Action-Whitelist), api_client.py (Async HTTPS API-Client mit ServerState/HealthInfo Dataclasses), whitelist.py und blacklist.py (JSON-basierte Spielerlisten), blueprint_manager.py (Upload/Download/Kategorisierung von Blueprints), savegame_stats.py (Savegame-Auflistung und Metadaten), savegame_analyzer.py (Tiefen-Analyse via satisfactory-save Package), settings_backup.py (API-basiertes Settings-Backup als JSON), save_header.py (Binary-Parser fuer SAV-Datei-Header).

### 2.3 Minecraft Multi-Server Architektur

Die Minecraft-Integration nutzt ein Prefix-basiertes ENV-System: Jeder Server wird ueber `MC_{SERVER_ID}_*` Variablen konfiguriert. Aktuell unterstuetzte Server-IDs sind `BMC` (Better MC) und `VANILLA`.

Pro Server existieren separate Instanzen fuer: MinecraftServer (systemd + RCON), MinecraftBackupManager (World-Backups), MinecraftChatBridge (Log-Polling + RCON), PlayerTracker (Spielzeit-Statistiken), CrashReplay (Log-Analyse), StatsTracker (Uptime + Player-Counts).

Die Server-Instanzen werden beim Bot-Start automatisch erstellt, sofern die jeweilige `MC_{ID}_SERVICE` ENV-Variable gesetzt ist. Ist sie nicht gesetzt oder leer, wird der Server uebersprungen.

### 2.4 Modul-Uebersicht

**modules/satisfactory/ (9 Dateien):** server.py (SatisfactoryServer-Klasse mit systemd-Steuerung und Action-Whitelist), api_client.py (Async HTTPS API-Client mit ServerState/HealthInfo Dataclasses), whitelist.py (JSON-basierte Whitelist mit async I/O), blacklist.py (JSON-basierte Banlist), blueprint_manager.py (Blueprint-Upload/Download mit 6 Kategorien und Metadaten-DB), savegame_stats.py (Savegame-Auflistung mit Metadaten), savegame_analyzer.py (Tiefen-Analyse via satisfactory-save 0.9.0+), settings_backup.py (API-basiertes Settings-Backup als JSON), save_header.py (Binary-Parser fuer SAV-Header mit struct und Windows FILETIME).

**modules/minecraft/ (8 Dateien):** server.py (MinecraftServer-Klasse mit systemd-Steuerung und RCON), rcon.py (Async RCON-Client mit signed ints, Reconnect und asyncio.Lock), backup.py (World-Backup-Manager mit async I/O und Path-Traversal-Schutz), chat_bridge.py (Bidirektionale Chat-Bridge mit Log-Polling und RCON-Injection-Schutz), settings_backup.py (server.properties Backup/Restore), update_checker.py (Paper API Build-Vergleich, nur Vanilla), blacklist.py (Serveruebergreifendes Ban-System mit Historie und JSON-Persistenz), modpack_updater.py (Modrinth/CurseForge API Modpack-Version-Check).

**modules/monitoring/ (15 Dateien):** health_check.py, performance.py, player_tracker.py, update_checker.py, stats_tracker.py, crash_replay.py, player_ip_tracker.py, login_audit.py, auto_cleanup.py, selftest.py, savegame_protection.py, graceful_degradation.py, steam_changelog.py, optimizer.py, web_status.py (HTML-Status-Seite via Jinja2-Template).

**modules/backup/ (3 Dateien):** backup_manager.py, onedrive_backup.py, config_backup.py.

**modules/notifications/ (2 Dateien):** discord_notifier.py, email_notifier.py.

**Standalone-Module (7 Dateien):** restart_timer.py, word_filter.py, anti_spam.py, command_logger.py, config_validator.py, maintenance.py, mod_manager.py.

**utils/ (5 Dateien):** config.py (.env + config.json Laden), logger.py (Logging Setup), formatting.py (Embed-Formatierung, Fortschrittsbalken), permissions.py (Berechtigungspruefung mit admin_only/owner_only Decorators und server_online_required Decorator).

---

## 3. Satisfactory — Detailbeschreibung

### 3.1 Server-Steuerung (systemd)

Die Klasse `SatisfactoryServer` (modules/satisfactory/server.py) steuert den Dedicated Server ueber systemd. Alle systemctl-Aufrufe verwenden `asyncio.create_subprocess_exec()` mit einer `_ALLOWED_ACTIONS` Whitelist (frozenset: start, stop, restart, status, is-active, show), um Command-Injection zu verhindern. Der Server laeuft unter dem Linux-User `satisfactory` mit dem Service `satisfactory.service`. Verfuegbare Operationen: `is_running()` prueft den Prozess-Status, `start()` / `stop()` / `restart()` steuern den Service, `get_status()` liefert PID, CPU-Last und RAM-Verbrauch via psutil.

### 3.2 HTTPS API-Client

Die Klasse `SatisfactoryAPI` (modules/satisfactory/api_client.py) kommuniziert mit der offiziellen Satisfactory Dedicated Server HTTPS API. Die API ist POST-basiert und erreichbar ueber `https://{host}:{port}/api/v1`. Authentifizierung erfolgt ueber einen Bearer-Token (`API_TOKEN`). SSL-Verifizierung ist konfigurierbar (`API_VERIFY_SSL=false` fuer Self-Signed Zertifikate).

Rueckgabedaten werden in Dataclasses gemappt: `ServerState` enthaelt Spielerzahl, Spielerlimit, Tech-Tier, Game-Phase, durchschnittliche Tick-Rate und Spielzeit. `HealthInfo` liefert den Server-Health-Status. Weitere API-Funktionen: `query_server_state()` fuer den aktuellen Status, `get_server_options()` fuer Einstellungen, `get_advanced_game_settings()` fuer erweiterte Settings, `save_game()` zum Speichern, `load_game()` zum Laden, `set_admin_password()` / `set_client_password()` fuer Passwort-Management, `kick_player()` und `get_player_list()` fuer Spieler-Verwaltung.

### 3.3 Savegame-System

Das Savegame-System besteht aus drei Modulen:

**save_header.py** parst den binaeren Header von .sav Dateien mittels Python `struct`. Extrahiert: Header-Version, Save-Version, Build-Version, Map-Name, Session-Name, Spielzeit (Sekunden) und Speicherdatum (Windows FILETIME Ticks nach datetime Konvertierung). Strings werden als Length-Prefixed UTF-8 gelesen.

**savegame_stats.py** (Klasse `SavegameStats`) listet alle verfuegbaren Savegames auf, sortiert nach Aenderungsdatum. Liefert pro Save: Dateiname, Groesse (human-readable), letztes Aenderungsdatum und geparste Header-Informationen. Maximal 20 Ergebnisse.

**savegame_analyzer.py** nutzt das externe Package `satisfactory-save` (Version 0.9.0+) fuer eine Tiefenanalyse. Parst SaveObject, FActorSaveHeader und FObjectSaveHeader. Liefert `WorldStats` Dataclass mit detaillierten Welt-Informationen. Falls das Package nicht installiert ist, wird graceful auf den einfachen Header-Parser zurueckgefallen.

### 3.4 Blueprint-Management

Die Klasse `BlueprintManager` (modules/satisfactory/blueprint_manager.py) verwaltet Blueprint-Dateien im Verzeichnis `/home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames/blueprints/`. Jeder Satisfactory-Blueprint besteht aus zwei Dateien: einer `.sbp`-Datei (Blueprint-Daten) und einer `.sbpcfg`-Datei (Blueprint-Konfiguration). Beide Dateien muessen zusammen vorhanden sein, damit der Blueprint im Spiel funktioniert.

**Upload-Prozess:** Der Upload unterstuetzt zwei Varianten: Entweder eine ZIP-Datei, die serverseitig entpackt wird, oder zwei einzelne Dateien (eine `.sbp` + eine `.sbpcfg`). In beiden Faellen wird validiert, dass genau eine `.sbp` und eine `.sbpcfg` Datei vorhanden sind. Fehlt eine der beiden Dateien oder wird ein ungueltiges Format hochgeladen, wird der Upload mit einer Fehlermeldung abgelehnt und nicht ausgefuehrt. Die validierten Dateien werden im Blueprint-Verzeichnis abgelegt.

6 vordefinierte Kategorien: Produktion, Logistik, Deko/Architektur, Energie, Transport/Zuege, Sonstiges. Metadaten (Uploader, Kategorie, Beschreibung, Upload-Datum) werden in einer JSON-Datenbank persistiert.

Operationen: Upload (ZIP entpacken oder 2 Einzeldateien, Validierung auf .sbp + .sbpcfg), Download (beide Dateien als ZIP via Discord-Attachment), List (nach Kategorie filterbar), Delete (Admin-only, entfernt beide Dateien + Metadaten-Cleanup).

### 3.5 Whitelist & Blacklist

Zwei identisch aufgebaute Module (`WhitelistManager` und `BlacklistManager`) verwalten Spielerlisten in JSON-Dateien (`data/whitelist.json` und `data/blacklist.json`). Beide bieten: async Load/Save, Spieler hinzufuegen/entfernen, Liste anzeigen, Enable/Disable Toggle. Die Whitelist kann optional erzwungen werden — ist sie aktiv, koennen nur gelistete Spieler dem Server beitreten.

### 3.6 Settings-Backup

Die Klasse `SettingsBackup` (modules/satisfactory/settings_backup.py) sichert und stellt Server-Einstellungen ueber die API wieder her. Erfasst werden `ServerOptions` und `AdvancedGameSettings` als JSON. Backups sind mit Zeitstempel versehen und werden lokal gespeichert. Restore sendet die gesicherten Settings zurueck an die API.

### 3.7 Update-Mechanismus (SteamCMD)

Der `UpdateChecker` (modules/monitoring/update_checker.py) prueft alle 6 Stunden auf neue Satisfactory-Versionen. Er vergleicht die lokal installierte Build-ID mit der neuesten verfuegbaren Build-ID ueber SteamCMD (`app_info_print`). Bei einer neuen Version wird eine Benachrichtigung im Admin-Channel gesendet. Das Update selbst wird ueber `/sat_update` ausgeloest, welches SteamCMD `+app_update` ausfuehrt. Der `SteamChangelog` (modules/monitoring/steam_changelog.py) kann zusaetzlich Aenderungsnotizen abrufen.

---

## 4. Minecraft — Detailbeschreibung

### 4.1 Multi-Server Architektur

Jeder MC-Server wird ueber `MC_{SERVER_ID}_*` ENV-Variablen konfiguriert. Die `MinecraftServer`-Klasse (modules/minecraft/server.py) kapselt systemd-Steuerung und RCON-Verbindung. Pro Server werden beim Bot-Start automatisch Instanzen erstellt fuer: Server-Steuerung, Backup, Chat-Bridge, Player-Tracking, Crash-Replay und Stats-Tracking.

### 4.2 RCON-Client

Der async RCON-Client (modules/minecraft/rcon.py) implementiert das Minecraft RCON-Protokoll mit signed 32-bit Integers, automatischem Reconnect und Bounded Loops. Verbindungen werden als async Context Manager verwaltet (`async with MinecraftRCON(...) as rcon`).

### 4.3 Chat-Bridge

Bidirektionale Chat-Bridge (modules/minecraft/chat_bridge.py) mit Log-Polling alle 5 Sekunden. Erkennt via Regex: Chat-Nachrichten, Join/Leave Events, Advancements und Deaths. Mention-Injection-Schutz via `AllowedMentions.none()`. Discord nach MC via RCON mit Rate-Limiting und RCON-Injection-Schutz.

### 4.4 Backup-System

World-Backup-Manager (modules/minecraft/backup.py) mit async I/O, automatischem Cleanup und Path-Traversal-Schutz. Vor jedem Backup wird `save-all` via RCON ausgefuehrt.

### 4.5 Update-Checker (Paper API)

Der `MinecraftUpdateChecker` (modules/minecraft/update_checker.py) vergleicht den installierten Paper-Build mit dem neuesten verfuegbaren Build ueber die Paper API. Nur fuer Vanilla/Paper aktiv, nicht fuer BMC.

### 4.6 Blacklist-System

Die Klasse `MinecraftBlacklist` (modules/minecraft/blacklist.py) implementiert ein serveruebergreifendes Ban-System fuer Minecraft. Ein Ban wird automatisch auf allen konfigurierten MC-Servern via RCON durchgesetzt. Das System fuehrt eine Ban-Historie mit Grund, Zeitstempel und ausfuehrendem Admin. Jeder Eintrag hat einen active/inactive Status. Bei `/mc players ban` wird der Spieler automatisch auch in die Blacklist aufgenommen. Daten werden in `data/mc_blacklist.json` persistiert.

Operationen: `/mc blacklist add <name> [grund]` bannt einen Spieler auf allen Servern, `/mc blacklist remove <name>` hebt den Ban auf, `/mc blacklist list` zeigt alle aktiven Bans, `/mc blacklist history [name]` zeigt die vollstaendige Ban-Historie.

### 4.7 Modpack-Update-Check

Die Klasse `ModpackUpdater` (modules/minecraft/modpack_updater.py) prueft automatisch alle 12 Stunden auf neue Modpack-Versionen. Unterstuetzt zwei APIs: Modrinth (bevorzugt, kein API-Key noetig) und CurseForge (Fallback, benoetigt `CURSEFORGE_API_KEY`). Bei einer neuen Version wird eine Benachrichtigung im Admin-Channel gesendet. Der manuelle Check erfolgt ueber `/mc config modpack_check`. Die Konfiguration erfordert `MC_BMC_MODPACK_ID` (Projekt-ID) und `MC_BMC_MODPACK_VERSION` (aktuell installierte Version).

---

## 5. Slash Commands — Vollstaendige Referenz

### 5.1 Satisfactory-Commands (GameServer Bot)

**Server-Steuerung:**
`/sat status` zeigt den aktuellen Server-Status inkl. API-Details: Spielerzahl, Tech-Tier, Game-Phase, Tick-Rate, Spielzeit (Berechtigung: Alle). `/sat start` startet den Server (Admin). `/sat stop` stoppt den Server mit 5-Minuten-Countdown und In-Game-Warnungen (Admin). `/sat restart` startet den Server mit 10-Minuten-Countdown neu (Admin). `/sat cancel` bricht einen laufenden Timer ab (Admin).

**Spieler-Verwaltung:**
`/sat_players` zeigt alle Online-Spieler (Spieler). `/sat_kick` kickt einen Spieler (Admin). `/sat_ban` bannt einen Spieler permanent (Admin). `/sat_unban` hebt einen Ban auf (Admin). `/whitelist add/remove/list` verwaltet die Whitelist (Admin). `/blacklist add/remove/list` verwaltet die Blacklist (Admin).

**Backup & Savegames:**
`/sat_backup` erstellt ein manuelles Backup (Spieler). `/sat_save` speichert den aktuellen Spielstand via API (Spieler). `/sat_download` laedt ein Savegame als Discord-Attachment herunter (Spieler). `/sat_backups_list` listet alle Backups mit Groesse und Datum auf (Spieler). `/sat_restore` stellt ein Backup wieder her — stoppt den Server, kopiert das Backup, startet neu (Owner). `/sat_stats` zeigt Savegame-Statistiken: Session-Name, Spielzeit, Build-Version, Dateigroesse (Spieler).

**Konfiguration:**
`/sat_settings` zeigt Server-Einstellungen via API: Spielerlimit, Autosave, Netzwerk-Qualitaet (Spieler). `/sat_playerlimit` aendert das Spielerlimit via API (Admin). `/sat_autosave` aendert das Autosave-Intervall (Admin). `/sat_console` fuehrt einen beliebigen API-Befehl aus (Owner). `/sat_load` laedt ein bestimmtes Savegame via API (Owner). `/sat_update` aktualisiert den Server via SteamCMD — stoppt den Server, fuehrt Update aus, startet neu (Owner).

**Blueprints:**
`/sat_blueprints_upload` laedt einen Blueprint als ZIP hoch mit Kategorieauswahl (Spieler). `/sat_blueprints_list` listet Blueprints auf, filterbar nach Kategorie (Spieler). `/sat_blueprints_download` laedt einen Blueprint als Discord-Attachment herunter (Spieler). `/sat_blueprints_delete` loescht einen Blueprint inkl. Metadaten (Admin).

### 5.2 Minecraft-Commands (GameServer Bot)

**Server-Steuerung:**
`/mc status [server]` zeigt den Status eines oder aller MC-Server (Alle). `/mc start <server>` startet einen Server mit Countdown (Admin). `/mc stop <server>` stoppt einen Server mit In-Game-Warnung (Admin). `/mc restart <server>` startet einen Server neu (Admin). `/mc cancel` bricht einen laufenden Timer ab (Admin).

**Spieler-Verwaltung:**
`/mc players list [server]` zeigt Online-Spieler (Spieler). `/mc players kick <name> [server]` kickt einen Spieler via RCON (Admin). `/mc players ban <name> [server]` bannt einen Spieler und traegt ihn in die Blacklist ein (Admin). `/mc whitelist add/remove/list [server]` verwaltet die Whitelist (Admin). `/mc blacklist add <name> [grund]` bannt einen Spieler serveruebergreifend (Admin). `/mc blacklist remove <name>` hebt einen Ban auf (Admin). `/mc blacklist list` zeigt alle aktiven Bans (Admin). `/mc blacklist history [name]` zeigt die Ban-Historie (Admin).

**Backup:**
`/mc backup create <server>` erstellt ein World-Backup (Spieler). `/mc backup list <server>` listet vorhandene Backups (Spieler). `/mc backup restore <server> <backup>` stellt ein Backup wieder her (Owner).

**Admin-Befehle:**
`/mc command <cmd> [server]` fuehrt einen RCON-Befehl aus (Owner). `/mc say <nachricht> [server]` sendet einen Broadcast (Admin). `/mc difficulty <level> [server]` aendert den Schwierigkeitsgrad (Admin). `/mc weather <typ> [server]` aendert das Wetter (Admin). `/mc time <zeit> [server]` setzt die Tageszeit (Admin). `/mc gamemode <modus> <spieler> [server]` aendert den Spielmodus (Admin).

**Konfiguration:**
`/mc config settings [server]` zeigt server.properties an (Admin). `/mc config set <key> <value> [server]` aendert eine Einstellung und schreibt sie in server.properties (Owner). `/mc config backup [server]` erstellt ein Config-Backup (Admin). `/mc config restore [server]` stellt ein Config-Backup wieder her (Owner). `/mc config update` prueft auf Paper-Updates — nur fuer Vanilla/Paper (Admin). `/mc config stats [server]` zeigt World-Statistiken wie Groesse und Spielerzahl (Spieler). `/mc config autosave <intervall> [server]` setzt das Autosave-Intervall in Minuten — fuehrt sofort ein save-all aus und startet einen periodischen Task (Admin). `/mc config modpack_check` prueft manuell auf Modpack-Updates via Modrinth/CurseForge API (Admin).

**Server-Autocomplete:** Bei allen Commands mit `[server]` Parameter erscheint eine Autocomplete-Liste der aktivierten Server. Ist nur ein Server aktiv, wird dieser automatisch ausgewaehlt.

### 5.3 Allgemeine Commands (GameServer Bot)

`/help` zeigt alle verfuegbaren Commands (Alle). `/server` zeigt eine Server-Uebersicht mit System-Info: CPU, RAM, Disk, Uptime, SAT-Status, MC-Status (Alle). `/ping` zeigt die Bot-Latenz (Owner). `/reload <cog>` laedt einen Cog zur Laufzeit neu (Owner). `/clear <anzahl> [stunden] [von] [bis]` loescht Nachrichten mit Fortschrittsanzeige, Crash-Recovery und Datumsfilter (Admin). `/clear` ohne Parameter bricht einen laufenden Loeschvorgang ab (Admin). `/timeout <user> <dauer>` schaltet einen User temporaer stumm und kickt ihn aus dem Game-Server (Admin). `/schedule add <nachricht> <zeit> [channel] [wiederholung]` plant eine Nachricht mit relativer ("in 2h") oder absoluter ("20:00") Zeitangabe, optional mit Wiederholung: einmalig, taeglich oder woechentlich (Admin). `/schedule list` zeigt alle geplanten Nachrichten (Admin). `/schedule cancel <id>` bricht eine geplante Nachricht ab (Admin).

### 5.4 Monitor Bot Commands

`/performance` zeigt System-Performance: CPU, RAM, Disk (Spieler). `/dashboard` aktualisiert das Dashboard-Embed manuell (Admin). `/stats [spieler]` zeigt Satisfactory-Spieler-Statistiken (Spieler). `/report [tage]` generiert einen Satisfactory-Wochenbericht mit Uptime, Peak-Spielern und Savegame-Groesse (Spieler). `/mcstats [spieler] [server]` zeigt Minecraft-Spieler-Statistiken (Spieler). `/mcreport [server]` generiert einen Minecraft-Wochenbericht (Spieler). `/mccrashlog [server]` zeigt die letzten Crash-Logs eines MC-Servers (Admin). `/scheduler` zeigt den Scheduler-Status und Konfiguration aller Tasks (Admin). `/update_check` sucht manuell nach Updates — SAT via SteamCMD, MC via Paper API (Admin). `/email test|status` testet oder zeigt den Status der E-Mail-Benachrichtigungen (Admin). `/onedrive status|upload|list` verwaltet OneDrive Cloud-Backups (Admin). `/backup stats` zeigt detaillierte Backup-Statistiken pro Server: Anzahl Backups, Gesamtgroesse, aeltestes/neuestes Backup, Disk-Usage mit Fortschrittsbalken und Farbcodierung (Spieler).

---

## 6. Monitoring & Automatisierung

### 6.1 Health Checks

**Satisfactory (alle 2 Minuten):** Prueft API-Erreichbarkeit und Prozess-Status via psutil. Bei Ausfall wird nach 3 aufeinanderfolgenden Fehlschlaegen (6 Minuten) eine Downtime-Benachrichtigung an den Admin-Channel gesendet. Crash-Detection mit Auto-Restart (max 5 pro Stunde, 30s Wartezeit). Bei Recovery wird eine Wiederherstellungs-Nachricht gesendet.

**Minecraft (alle 2 Minuten):** Prueft Prozess-Status via systemd und RCON-Erreichbarkeit. Nach 3 Fehlschlaegen (6 Minuten) wird eine Downtime-Benachrichtigung an den Admin-Channel gesendet. Bei Wiedererreichbarkeit folgt eine Recovery-Nachricht.

### 6.2 Auto-Backup

**Satisfactory:** Alle 6 Stunden automatisches Savegame-Backup. Lokale Speicherung unter `/home/botuser/Discord_Bots/backups/`. OneDrive Cloud-Upload via rclone ist verpflichtend und wird automatisch nach jedem Backup ausgefuehrt.

**Minecraft:** Alle 6 Stunden automatisches World-Backup pro Server. Vor dem Backup wird `save-all` via RCON ausgefuehrt. Lokale Speicherung unter `/home/minecraft/backups/{server_id}/`. Automatisches Cleanup alter Backups. OneDrive Cloud-Upload nach `MinecraftBackups/{server_id}/` ist verpflichtend.

### 6.3 Daily Restart

Taeglicher Neustart um 04:00 Uhr fuer alle Server. Wird nur ausgefuehrt wenn der Server laenger als 12 Stunden laeuft. Wird uebersprungen wenn Spieler online sind. Bei Satisfactory: Restart-Warnung 2 Minuten vorher im Admin-Channel. Bei Minecraft: In-Game-Warnungen 2 Minuten, 1 Minute und direkt vor dem Restart via RCON.

### 6.4 Player-Tracking

Separate PlayerTracker-Instanz pro Server (SAT + MC). Trackt Join/Leave-Events, berechnet Spielzeit pro Spieler, und generiert Wochenberichte. Daten werden in `data/` (SAT) bzw. `data/mc_{server_id}/` (MC) als JSON persistiert. Wochenberichte enthalten: Gesamte Spielzeit, Peak-Spielerzahl, aktivste Spieler.

### 6.5 Update-Checks

**Satisfactory:** Alle 6 Stunden via SteamCMD Build-ID Vergleich. Bei neuer Version Benachrichtigung im Admin-Channel mit alter und neuer Build-ID.

**Minecraft Vanilla/Paper:** Alle 6 Stunden via Paper API. Vergleicht den aktuellen Build mit dem neuesten verfuegbaren Build. Nur fuer Vanilla/Paper aktiv, nicht fuer Better MC (Modpack-Updates sind manuell).

### 6.6 Crash Replay

Bei Server-Crash werden die letzten Log-Zeilen (Buffer: 50 Zeilen) analysiert und als Zusammenfassung im Admin-Channel gepostet. Unterstuetzt sowohl Satisfactory-Crashes als auch Minecraft-Crashes. MC-spezifische Error-Keywords: OutOfMemoryError, ConcurrentModificationException, StackOverflowError, CrashReport, FATAL, etc.

### 6.7 Selftest

`/selftest` prueft 17 Subsysteme: Discord-Channel-Erreichbarkeit, Satisfactory API-Verbindung, SAT-Prozess-Status, UFW-Firewall, Festplatte (Warnung bei >90%), Savegame-Pfad, OneDrive-Konfiguration, E-Mail-Setup, SteamCMD-Verfuegbarkeit, Config-Dateien (.env + config.json). Fuer jeden konfigurierten MC-Server zusaetzlich: Server-Status, RCON-Erreichbarkeit, Log-Pfad-Existenz, Backup-Pfad-Schreibrechte.

### 6.8 Weitere Monitoring-Features

**Performance-Monitor (alle 5 Minuten):** Erfasst CPU, RAM, Disk und Netzwerk. Bei Ueberschreitung konfigurierbarer Schwellwerte wird eine Warnung gesendet.

**Dashboard-Embed (alle 10 Minuten):** Auto-Update Embed im Status-Channel mit SAT + MC Server-Status, Spielerzahlen, System-Performance.

**Voice-Channel Stats (alle 5 Minuten):** Aktualisiert Voice-Channel-Namen mit Live-Daten wie "SAT: 2/4" und "CPU: 45% | RAM: 62%".

**Login Audit:** Ueberwacht SSH-Login-Versuche und meldet verdaechtige Aktivitaet.

**Auto-Cleanup:** Bereinigt alte Log-Dateien und temporaere Daten.

**Savegame Protection:** Ueberwacht Savegame-Integritaet und warnt bei Korruption.

**Graceful Degradation:** Bei Teilausfaellen (z.B. API nicht erreichbar) laufen die uebrigen Features weiter.

**Server Optimizer:** Analysiert Server-Performance und gibt Optimierungsvorschlaege.

### 6.9 Web-Status-Seite

Das Modul `WebStatus` (modules/monitoring/web_status.py) generiert eine statische HTML-Seite mit dem aktuellen Status aller Server. Die Seite wird alle 60 Sekunden aktualisiert und als HTML-Datei in ein konfigurierbares Verzeichnis geschrieben (Standard: `/var/www/status`). Das Template (`templates/status.html`) nutzt Jinja2 und bietet ein Dark-Mode Design mit responsivem Layout. Angezeigt werden: Server-Status (Online/Offline) fuer SAT und alle MC-Server, aktuelle Spielerzahlen, System-Performance (CPU, RAM, Disk) und letztes Update-Datum.

Die Web-Status-Seite ist standardmaessig deaktiviert und wird ueber `WEB_STATUS_ENABLED=true` aktiviert. Fuer den externen Zugriff wird Nginx als Reverse-Proxy empfohlen — das Setup-Script `scripts/setup_nginx.sh` konfiguriert dies automatisch.

### 6.10 Scheduled Messages

Der erweiterte `SchedulerCog` (cogs/scheduler_cog.py) unterstuetzt geplante Nachrichten ueber `/schedule add`. Zeitangaben koennen relativ ("in 2h", "in 30m") oder absolut ("20:00", "2026-02-25 14:00") sein. Unterstuetzte Wiederholungsoptionen: einmalig (Standard), taeglich und woechentlich. Alle Zeiten werden in der Zeitzone Europe/Berlin verarbeitet. Maximal 20 aktive Schedules sind erlaubt, Nachrichten sind auf 2000 Zeichen begrenzt. Daten werden in `data/scheduled_messages.json` persistiert und ueberleben Bot-Neustarts.

---

## 7. ENV-Variablen Referenz

### 7.1 Discord

**Pflicht:** `DISCORD_TOKEN_MANAGER` (GameServer Bot Token), `DISCORD_TOKEN_WATCHDOG` (Monitor Bot Token), `GUILD_ID` (Discord Server ID), `OWNER_ID` (Bot-Owner User ID), `ADMIN_ROLE_ID` (Admin-Rolle), `SATISFACTORY_ROLE_ID` (Spieler-Rolle SAT), `ADMIN_LOG_CHANNEL_ID` (Admin-Log Channel), `PUBLIC_STATUS_CHANNEL_ID` (Oeffentlicher Status-Channel).

**Optional:** `STATUS_EMBED_CHANNEL_ID` (Dashboard-Embed Channel), `VOICE_STATS_CATEGORY_ID` (Voice-Channel Stats Kategorie), `NOTIFY_ROLE_ID` (Benachrichtigungs-Rolle), `MINECRAFT_ROLE_ID` (MC-Spieler-Rolle, 0 = deaktiviert).

### 7.2 Satisfactory

**Server:** `SATISFACTORY_SERVICE` (systemd Service-Name, Standard: satisfactory.service), `SATISFACTORY_USER` (Linux-User, Standard: satisfactory), `SATISFACTORY_SERVER_PATH` (Installationspfad), `SATISFACTORY_SAVE_PATH` (Savegame-Verzeichnis).

**API:** `API_HOST` (Standard: 127.0.0.1), `API_PORT` (Standard: 7777), `API_TOKEN` (Authentifizierungs-Token, Pflicht), `API_VERIFY_SSL` (SSL-Verifizierung, Standard: false fuer Self-Signed).

**Updates:** `STEAMCMD_PATH` (SteamCMD-Pfad, Standard: /usr/games/steamcmd).

### 7.3 Minecraft Multi-Server

Pro Server mit Prefix `MC_{SERVER_ID}_*`:

`MC_{ID}_SERVICE` — systemd Service-Name (Pflicht, aktiviert den Server). `MC_{ID}_DISPLAY_NAME` — Anzeigename in Discord. `MC_{ID}_PATH` — Server-Installationspfad. `MC_{ID}_WORLD_PATH` — World-Verzeichnis. `MC_{ID}_RCON_HOST` — RCON-Host (Standard: 127.0.0.1). `MC_{ID}_RCON_PORT` — RCON-Port. `MC_{ID}_RCON_PASSWORD` — RCON-Passwort. `MC_{ID}_BACKUP_PATH` — Backup-Verzeichnis. `MC_{ID}_LOG_PATH` — Pfad zu latest.log. `MC_{ID}_GAME_CHAT_CHANNEL_ID` — Discord Chat-Bridge Channel (0 = deaktiviert).

Aktuell konfigurierte Server-IDs: `BMC` (Better MC, Ports 25566/25575) und `VANILLA` (Paper, Ports 25565/25576).

### 7.4 Backup & Cloud

`BACKUP_PATH` (Lokaler Backup-Ordner, Pflicht), `ONEDRIVE_ENABLED` (Pflicht, muss `true` sein — Cloud-Backup ist verpflichtend), `ONEDRIVE_REMOTE` (rclone Remote-Name, Pflicht), `ONEDRIVE_PATH` (Remote-Pfad, Pflicht).

### 7.5 E-Mail (Optional)

`EMAIL_ENABLED` (true/false), `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_FROM`, `EMAIL_TO`.

### 7.6 Web-Status (Optional)

`WEB_STATUS_ENABLED` (true/false, Standard: false), `WEB_STATUS_PATH` (Ausgabeverzeichnis, Standard: /var/www/status).

### 7.7 Modpack-Updates (Optional)

`MC_BMC_MODPACK_ID` (Modrinth/CurseForge Projekt-ID), `MC_BMC_MODPACK_VERSION` (aktuell installierte Version), `MC_BMC_MODPACK_SOURCE` (modrinth oder curseforge, Standard: modrinth), `CURSEFORGE_API_KEY` (nur bei CurseForge-Quelle noetig).

### 7.8 Verschluesselung (Optional)

`GPG_PASSPHRASE` (Passphrase fuer GPG AES256-Verschluesselung der Config-Backups). Erfordert installiertes `gpg` auf dem Server.

---

## 8. Server-Infrastruktur

### 8.1 Hardware

Netcup RS 4000 G12: 12 vCores (AMD EPYC 9645), 32 GB RAM, ca. 950 GB freier Speicher (NVMe). Ubuntu 22.04 LTS mit systemd. IP: 203.0.113.10, SSH-Port: 4422.

### 8.2 Dienste und Ports

GameServer Bot (`gameserver-bot.service`) — kein externer Port. Monitor Bot (`monitor-bot.service`) — kein externer Port. Satisfactory (`satisfactory.service`) — Ports 7777, 15000, 15777 (UDP/TCP). MC Vanilla/Paper (`minecraft-vanilla.service`) — Port 25565 (Game, oeffentlich), Port 25576 (RCON, nur lokal). MC Better MC (`minecraft-bmc.service`) — Port 25566 (Game, oeffentlich), Port 25575 (RCON, nur lokal).

### 8.3 RAM-Aufteilung

Satisfactory: variabel (bestehende Konfiguration, ca. 8-12 GB je nach Factory-Groesse). MC Vanilla/Paper: 2-4 GB (-Xms2G -Xmx4G), MemoryMax 6 GB. MC Better MC: 4-8 GB (-Xms4G -Xmx8G), MemoryMax 10 GB. Discord Bots: ca. 200-500 MB. System + Reserve: ca. 8 GB.

### 8.4 systemd Services

**Satisfactory:** Service `satisfactory.service`, User: satisfactory. Restart-on-failure.

**Minecraft:** Beide MC-Services verwenden Aikar's G1GC JVM-Flags fuer optimale Garbage Collection. Graceful Shutdown via `rcon-cli stop` (ExecStop). Restart-on-failure mit maximal 3 Neustarts in 600 Sekunden. Resource-Limits (MemoryMax, CPUQuota=200%, LimitNOFILE=65536). User: minecraft, Group: minecraft.

**Discord Bots:** `gameserver-bot.service` und `monitor-bot.service`. User: botuser. Restart-on-failure. WorkingDirectory: /home/botuser/Discord_Bots.

### 8.5 SSH-Zugang

`ssh netcup-marco` verbindet als marco (sudo-Befehle). `ssh netcup-botuser` verbindet als botuser (SCP-Uploads). Konfiguriert in `C:\Users\Marco\.ssh\config`, Port 4422.

### 8.6 Deployment-Workflow

Code-Aenderungen werden lokal entwickelt und per SCP hochgeladen:
```
scp -P 4422 -r modules/* netcup-botuser:/home/botuser/Discord_Bots/modules/
scp -P 4422 -r cogs/* netcup-botuser:/home/botuser/Discord_Bots/cogs/
scp -P 4422 -r bots/* netcup-botuser:/home/botuser/Discord_Bots/bots/
scp -P 4422 -r utils/* netcup-botuser:/home/botuser/Discord_Bots/utils/
```
Anschliessend Services neustarten: `sudo systemctl restart gameserver-bot monitor-bot`. Logs pruefen: `journalctl -u gameserver-bot -n 50 --no-pager`.

---

## 9. Sicherheit

In Phase 7 (v3.1.0) wurde ein Komplett-Review ueber alle 63 Python-Dateien durchgefuehrt. 28 CRITICAL-Befunde und 8 WARNING-Befunde wurden behoben.

**RCON-Injection-Schutz:** Alle Nachrichten die via RCON an Minecraft gesendet werden, durchlaufen eine Sanitisierung ueber `_sanitize_rcon_input()` mit Whitelist erlaubter Zeichen. Zusaetzlich werden Minecraft Target-Selektoren (`@a`, `@p`, `@e`, `@r`, `@s`) gefiltert. User-Input in RCON-Befehlen (Spielernamen, Gruende) wird validiert.

**Mention-Injection-Schutz:** Alle Nachrichten die von Minecraft nach Discord weitergeleitet werden, nutzen `AllowedMentions.none()`. Spielernamen und Chat-Nachrichten werden zusaetzlich mit `discord.utils.escape_markdown()` und `escape_mentions()` behandelt. Dies gilt fuer alle Module: Chat-Bridge, Discord-Notifier, Player-Tracker und Player-IP-Tracker.

**Path-Traversal-Schutz:** Backup-Restore und -Delete Operationen validieren Pfade mit `.resolve()` und pruefen ob der aufgeloeste Pfad innerhalb des erlaubten Backup-Verzeichnisses liegt. Der Mod-Import (`/mod import`) nutzt `Path().name` + `resolve()` + `is_relative_to()` zur Pruefung.

**Command-Injection-Prevention:** systemctl-Aufrufe nutzen eine `ALLOWED_ACTIONS` Whitelist (frozenset). Nur definierte Aktionen wie start, stop, restart, status, is-active und show sind erlaubt. Alle Subprocess-Aufrufe verwenden `create_subprocess_exec()` statt Shell-Interpolation. Der Satisfactory LoadGame-Befehl validiert Savenames auf alphanumerisch + Unterstrich + Bindestrich.

**API-Sicherheit:** Satisfactory API-Kommunikation erfolgt ueber HTTPS mit Bearer-Token. SSL-Verifizierung ist konfigurierbar. Session-Erstellung ist durch `asyncio.Lock` gegen Race Conditions geschuetzt.

**Race-Condition-Schutz:** RCON-Verbindungen nutzen `asyncio.Lock` um parallele Aufrufe zu serialisieren. Whitelist und Blacklist JSON-Dateien sind durch Locks gegen gleichzeitige Schreibzugriffe geschuetzt.

**Async-Sicherheit:** Blockierende Aufrufe wie `psutil.cpu_percent()` und synchrone File-I/O wurden in `asyncio.run_in_executor()` bzw. `asyncio.to_thread()` gewrappt, um den Event-Loop nicht zu blockieren.

**UFW/Player-IP-Tracking:** Der Player-IP-Tracker kann IPs von Spielern via UFW blocken. IP-Adressen werden vor Verwendung mit Regex auf gueltiges IPv4-Format geprueft.

**Berechtigungssystem:** Vierstufiges System: Owner (Bot-Besitzer, alle Rechte), Admin (Admin-Rolle, Server-Steuerung), Spieler (Spieler-Rolle, Info + Aktionen), Alle (nur lesende Befehle). Implementiert ueber `admin_only()`, `owner_only()` und `server_online_required()` Decorators in `utils/permissions.py`.

**Word Filter & Anti-Spam:** Konfigurierbare Wortfilter-Patterns (partial/exact/regex) und Rate-Limiting (5 Nachrichten/10s, 3 Commands/10s) fuer Discord-Moderation und MC Chat-Bridge. Index-Synchronisation zwischen Patterns und Wortliste wurde auf Tuple-basierte Zuordnung umgestellt.

**Config-Backup-Verschluesselung:** Optionale GPG AES256-Verschluesselung fuer Config-Backups (`.env` und `config.json`). Aktiviert durch `GPG_PASSPHRASE` ENV-Variable. Verhindert, dass Tokens und Passwoerter unverschluesselt in Cloud-Backups landen.

**Backup-Sicherheit:** `tar.extractall()` nutzt vorgefilterte `safe_members` Listen statt ungefilterte Extraktion.

---

## 10. Entwicklungshistorie

### v1.0.0 — Initiale Version (Januar 2026)

Grundlegendes 2-Bot-System fuer Satisfactory. Basis-Commands (start/stop/status), Health Check, einfache Backups, Dashboard-Embed, Daily Restart.

### v2.0.0 — Feature-Erweiterung (Februar 2026)

Erweiterte Satisfactory-Features: Blueprints mit Kategorien, Whitelist/Blacklist, Chat-Bridge, Savegame-Analyse (Header-Parser + satisfactory-save), SteamCMD-Updates, OneDrive-Backup via rclone, E-Mail-Benachrichtigungen, Player-Tracking mit Wochenberichten, Crash-Replay, Performance-Monitoring mit Schwellwerten, Voice-Channel Stats, Command Audit-Logging, Word Filter und Anti-Spam.

### v2.2.0 — Code-Review + Bugfixes (18. Februar 2026)

Umfassender Code-Review ueber 56 Dateien. 12 kritische Fehler behoben (Shell-Injection, Command-Injection, fehlende Imports, Async-Bugs). 8 Logik-Fehler behoben (Race Conditions, Nested Event Loops, Off-by-one). 52 Code-Qualitaet-Verbesserungen (Type Hints fuer 36 Dateien, 18 bare Exceptions, 13 Cog Error Handler). 6 Architektur-Optimierungen (sudoers Haertung, Watchdog-Service, Drop-Caches-Script).

### v3.1.0 — Sicherheits-Review + Feature-Erweiterung (20. Februar 2026, aktuell)

Komplett-Review ueber 63 Python-Dateien (Phase 7): 28 CRITICAL Fixes (Injection-Schutz, Race Conditions, asyncio-Bugs, fehlende Imports), 123 WARNING-Befunde dokumentiert, 8 WARNING Fixes. Neue Features (Phase 8a-8h): Server-Offline-Decorator fuer einheitliche Online-Pruefung, MC Autosave-Command via RCON, Backup-Statistiken mit Disk-Usage, Config-Backup Rotation mit optionaler GPG-Verschluesselung, MC Blacklist-System mit serveruebergreifenden Bans, Scheduled Messages mit relativen/absoluten Zeitangaben, Web-Status-Seite mit Jinja2-Template und Nginx-Setup, BMC Modpack-Update-Check via Modrinth/CurseForge API. Phase 9: Re-Review aller Phase 8 Dateien, /clear Abbruchfunktion mit Cancel-Event-System.

### v3.0.0 — Minecraft-Integration (20. Februar 2026)

Komplette Minecraft Multi-Server Integration (Phase 14a-14o, 18 Commits). 6 neue MC-Module (server, rcon, backup, chat_bridge, settings_backup, update_checker). Neue systemd Services (minecraft-vanilla, minecraft-bmc). MC-SAT Feature Parity: StatsTracker, CrashReplay, PlayerIPTracker, UpdateChecker fuer MC. Code-Review: 3 Critical, 12 Warning, 1 Bug behoben. SAT Chat-Bridge Cleanup (GAME_CHAT_CHANNEL_ID entfernt). ENV reorganisiert (9 Kategorien). Deployment auf Server abgeschlossen, beide Bots laufen fehlerfrei, Selftest 17/17.

---

## 11. Konfigurationsdateien

### config.json Struktur

Feature-Toggles, Intervalle und Schwellwerte. Jedes Feature kann einzeln aktiviert/deaktiviert werden. Satisfactory-Features: daily_restart, auto_backup, email_notifications, onedrive_backup. Minecraft-Scheduler unter `scheduler.minecraft.*`: `daily_restart_hour` (Standard: 4), `auto_backup_interval_hours` (Standard: 6), `update_check_interval_hours` (Standard: 6).

### .env.example

Vollstaendige Vorlage mit allen ENV-Variablen, organisiert in 9 uebersichtlichen Kategorien mit Box-Frame-Headern und erklaerenden Kommentaren. Kategorien: Discord Tokens & Server, Rollen & Berechtigungen, Channels, Satisfactory Server + API + SteamCMD, Minecraft BMC, Minecraft Vanilla, Backup & Cloud, E-Mail, Server-Info. Liegt unter `config/.env.example`.

---

## 12. Abschluss

Alle Phasen des Projekts sind abgeschlossen (Stand: 20. Februar 2026, Version 3.1.0):

**Server-Setup:** Satisfactory Dedicated Server laeuft mit HTTPS API. Beide Minecraft-Server (Vanilla/Paper + BMC3 Fabric) sind eingerichtet. Java 21, systemd Services, UFW-Regeln und rcon-cli sind installiert und konfiguriert.

**Discord-Integration:** Chat-Bridge Channels fuer beide MC-Server sind im Discord erstellt und konfiguriert. Die bidirektionale Chat-Bridge (Minecraft↔Discord) ist funktionsfaehig. Scheduled Messages ermoeglichen geplante Ankuendigungen.

**Sicherheit:** Komplett-Review ueber 63 Dateien mit 28 CRITICAL Fixes durchgefuehrt. Injection-Schutz, Race-Condition-Absicherung und async-sichere I/O in allen Modulen.

**Monitoring:** Web-Status-Seite (optional) fuer externen Zugriff auf Server-Status. BMC Modpack-Update-Check via Modrinth/CurseForge API. Backup-Statistiken mit Disk-Usage-Uebersicht. Config-Backup mit optionaler GPG-Verschluesselung.

**Deployment:** Alle Code-Dateien sind auf dem Server deployed. Beide Bots (GameServer Bot + Monitor Bot) laufen fehlerfrei. Selftest besteht alle 17 Tests.

**Dokumentation:** VERSION 3.1.0, README, CHANGELOG und diese Projektdokumentation sind aktualisiert. Die .env.example wurde mit neuen ENV-Variablen fuer Web-Status, Modpack-Updates und GPG-Verschluesselung erweitert.

### Neue Abhaengigkeiten (v3.1.0)

`jinja2` (Pflicht fuer Web-Status-Template), `aiofiles` (sollte bereits installiert sein). Optional: `gpg` (Systempaket fuer Config-Backup-Verschluesselung).

### Moegliche zukuenftige Erweiterungen

Satisfactory Chat-Bridge reaktivieren sobald die API stabile Chat-Endpoints bietet. Interaktives Web-Dashboard mit Login und Echtzeit-Updates (FastAPI + HTMX + Jinja2). Admin Bot als dritter Discord-Bot fuer Discord-Moderation, TeamSpeak-Steuerung und Temp Voice Channels. TeamSpeak-Integration (Server-Status, Chat-Bridge TS↔Discord, Channel-Management). MC World-Analyse per Command (`/mc world stats`). Datenbank-Migration von JSON zu SQLite.

### Verworfene/Zurueckgestellte Features

Erweiterte Spieler-Statistiken (Heatmaps, Streak-Tracking) — aktuell nicht benoetigt. Multi-Guild Support (mehrere Discord-Server) — erst relevant wenn tatsaechlich benoetigt.
