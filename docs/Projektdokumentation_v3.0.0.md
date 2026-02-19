# Discord Bot System — Projektdokumentation v3.0.0

> **Version:** 3.0.0 | **Datum:** 20. Februar 2026 | **Autor:** Marco

---

## 1. Projektuebersicht

Das Discord Bot System ist ein Zwei-Bot-System zur Verwaltung von Gameservern ueber Discord. Es steuert einen Satisfactory Dedicated Server sowie zwei Minecraft-Server (Vanilla/Paper + Better MC) auf einem dedizierten Linux-Server.

### Eckdaten

- 60 Python-Dateien, ca. 21.800 Zeilen Code
- 2 Discord-Bots, 8 Cogs, 40+ Module, 4 Utils
- ca. 70 Slash Commands
- Python 3.9+ mit discord.py 2.x
- Vollstaendig async (asyncio)
- systemd-Integration fuer alle Dienste

### Unterstuetzte Gameserver

Der GameServer Bot (Bot 1) steuert alle Server via Slash Commands. Der Monitor Bot (Bot 2) uebernimmt automatisierte Background-Tasks wie Health Checks, Backups und Chat-Bridges.

**Satisfactory:** Steuerung ueber die offizielle HTTP API (Port 7777). Savegame-Analyse, Blueprint-Management, Chat-Bridge, SteamCMD-Updates.

**Minecraft Vanilla/Paper:** Paper MC 1.21.4 Build 209. Steuerung ueber systemd + RCON (Port 25576 lokal). Bidirektionale Chat-Bridge via Log-Polling und RCON. Paper API Update-Check.

**Minecraft Better MC:** Forge/NeoForge Modpack. Steuerung ueber systemd + RCON (Port 25575 lokal). Bidirektionale Chat-Bridge. Kein automatischer Update-Check (Modpack-Updates manuell).

---

## 2. Architektur

### Bot-Aufteilung

**GameServer Bot (Bot 1):** Verarbeitet alle Slash Commands der Benutzer. Startet, stoppt und verwaltet Server. Fuehrt RCON-Befehle aus. Verwaltet Backups, Whitelist, Blacklist und Blueprints. Chat-Bridge fuer Satisfactory (API-basiert).

**Monitor Bot (Bot 2):** Fuehrt automatisierte Background-Tasks aus. Health Checks alle 2 Minuten (SAT + MC). Performance-Monitoring alle 5 Minuten. Dashboard-Embed alle 10 Minuten. Auto-Backups alle 6 Stunden. Daily Restart um 04:00. Chat-Bridge fuer Minecraft (Log-Polling alle 5 Sekunden). Player-Tracking (Join/Leave/Spielzeit). Update-Checks (SteamCMD + Paper API). Crash-Detection mit Auto-Restart.

### Minecraft Multi-Server Architektur

Die Minecraft-Integration nutzt ein Prefix-basiertes ENV-System: Jeder Server wird ueber `MC_{SERVER_ID}_*` Variablen konfiguriert. Aktuell unterstuetzte Server-IDs sind `BMC` (Better MC) und `VANILLA`.

Pro Server existieren separate Instanzen fuer: MinecraftServer (systemd + RCON), MinecraftBackupManager (World-Backups), MinecraftChatBridge (Log-Polling + RCON), PlayerTracker (Spielzeit-Statistiken), CrashReplay (Log-Analyse), StatsTracker (Uptime + Player-Counts).

Die Server-Instanzen werden beim Bot-Start automatisch erstellt, sofern die jeweilige `MC_{ID}_SERVICE` ENV-Variable gesetzt ist. Ist sie nicht gesetzt oder leer, wird der Server uebersprungen.

### Modul-Uebersicht

**modules/minecraft/ (6 Dateien):** server.py (MinecraftServer-Klasse mit systemd-Steuerung und RCON), rcon.py (Async RCON-Client mit signed ints und Reconnect), backup.py (World-Backup-Manager mit async I/O und Path-Traversal-Schutz), chat_bridge.py (Bidirektionale Chat-Bridge mit Log-Polling und RCON-Injection-Schutz), settings_backup.py (server.properties Backup/Restore), update_checker.py (Paper API Build-Vergleich, nur Vanilla).

**modules/satisfactory/ (9 Dateien):** server.py, api_client.py, whitelist.py, blacklist.py, blueprint_manager.py, savegame_stats.py, savegame_analyzer.py, settings_backup.py, save_header.py.

**modules/monitoring/ (13 Dateien):** health_check.py, performance.py, player_tracker.py, update_checker.py, stats_tracker.py, crash_replay.py, player_ip_tracker.py, login_audit.py, auto_cleanup.py, selftest.py, savegame_protection.py, graceful_degradation.py, steam_changelog.py, optimizer.py.

**modules/backup/ (3 Dateien):** backup_manager.py, onedrive_backup.py, config_backup.py.

**modules/notifications/ (2 Dateien):** discord_notifier.py, email_notifier.py.

**Standalone-Module (7 Dateien):** restart_timer.py, word_filter.py, anti_spam.py, command_logger.py, config_validator.py, maintenance.py, mod_manager.py.

**utils/ (4 Dateien):** config.py (.env + config.json Laden), logger.py (Logging Setup), formatting.py (Embed-Formatierung, Fortschrittsbalken), permissions.py (Berechtigungspruefung mit admin_only/owner_only Decorators).

---

## 3. Slash Commands — Vollstaendige Referenz

### 3.1 Satisfactory-Commands (GameServer Bot)

**Server-Steuerung:**
`/sat status` zeigt den aktuellen Server-Status inkl. API-Details (Berechtigung: Alle). `/sat start` startet den Server (Admin). `/sat stop` stoppt den Server mit 5-Minuten-Countdown und In-Game-Warnungen (Admin). `/sat restart` startet den Server mit 10-Minuten-Countdown neu (Admin). `/sat cancel` bricht einen laufenden Timer ab (Admin).

**Spieler-Verwaltung:**
`/sat_players` zeigt alle Online-Spieler (Spieler). `/sat_kick` kickt einen Spieler (Admin). `/sat_ban` bannt einen Spieler permanent (Admin). `/sat_unban` hebt einen Ban auf (Admin). `/sat_broadcast` sendet eine Nachricht an alle Spieler (Spieler). `/whitelist add/remove/list` verwaltet die Whitelist (Admin). `/blacklist add/remove/list` verwaltet die Blacklist (Admin).

**Backup & Savegames:**
`/sat_backup` erstellt ein manuelles Backup (Spieler). `/sat_save` speichert den aktuellen Spielstand (Spieler). `/sat_download` laedt ein Savegame herunter (Spieler). `/sat_backups_list` listet alle Backups auf (Spieler). `/sat_restore` stellt ein Backup wieder her (Owner). `/sat_stats` zeigt Savegame-Statistiken (Spieler).

**Konfiguration:**
`/sat_settings` zeigt Server-Einstellungen (Spieler). `/sat_playerlimit` aendert das Spielerlimit (Admin). `/sat_autosave` aendert das Autosave-Intervall (Admin). `/sat_console` fuehrt einen Konsolenbefehl aus (Owner). `/sat_load` laedt ein Savegame (Owner). `/sat_update` aktualisiert den Server via SteamCMD (Owner).

**Blueprints:**
`/sat_blueprints_upload` laedt einen Blueprint hoch (Spieler). `/sat_blueprints_list` listet Blueprints auf (Spieler). `/sat_blueprints_download` laedt einen Blueprint herunter (Spieler). `/sat_blueprints_delete` loescht einen Blueprint (Admin).

### 3.2 Minecraft-Commands (GameServer Bot)

**Server-Steuerung:**
`/mc status [server]` zeigt den Status eines oder aller MC-Server (Alle). `/mc start <server>` startet einen Server mit Countdown (Admin). `/mc stop <server>` stoppt einen Server mit In-Game-Warnung (Admin). `/mc restart <server>` startet einen Server neu (Admin). `/mc cancel` bricht einen laufenden Timer ab (Admin).

**Spieler-Verwaltung:**
`/mc players list [server]` zeigt Online-Spieler (Spieler). `/mc players kick <name> [server]` kickt einen Spieler via RCON (Admin). `/mc players ban <name> [server]` bannt einen Spieler (Admin). `/mc whitelist add/remove/list [server]` verwaltet die Whitelist (Admin).

**Backup:**
`/mc backup create <server>` erstellt ein World-Backup (Spieler). `/mc backup list <server>` listet vorhandene Backups (Spieler). `/mc backup restore <server> <backup>` stellt ein Backup wieder her (Owner).

**Admin-Befehle:**
`/mc command <cmd> [server]` fuehrt einen RCON-Befehl aus (Owner). `/mc say <nachricht> [server]` sendet einen Broadcast (Admin). `/mc difficulty <level> [server]` aendert den Schwierigkeitsgrad (Admin). `/mc weather <typ> [server]` aendert das Wetter (Admin). `/mc time <zeit> [server]` setzt die Tageszeit (Admin). `/mc gamemode <modus> <spieler> [server]` aendert den Spielmodus (Admin).

**Konfiguration:**
`/mc config settings [server]` zeigt server.properties an (Admin). `/mc config set <key> <value> [server]` aendert eine Einstellung und schreibt sie in server.properties (Owner). `/mc config backup [server]` erstellt ein Config-Backup (Admin). `/mc config restore [server]` stellt ein Config-Backup wieder her (Owner). `/mc config update` prueft auf Paper-Updates — nur fuer Vanilla/Paper (Admin). `/mc config stats [server]` zeigt World-Statistiken wie Groesse und Spielerzahl (Spieler).

**Server-Autocomplete:** Bei allen Commands mit `[server]` Parameter erscheint eine Autocomplete-Liste der aktivierten Server. Ist nur ein Server aktiv, wird dieser automatisch ausgewaehlt.

### 3.3 Allgemeine Commands (GameServer Bot)

`/help` zeigt alle verfuegbaren Commands (Alle). `/server` zeigt eine Server-Uebersicht mit System-Info (Alle). `/ping` zeigt die Bot-Latenz (Alle). `/reload <cog>` laedt einen Cog zur Laufzeit neu (Owner). `/selftest` fuehrt einen System-Selbsttest durch, inkl. MC-Checks (Admin). `/clear <anzahl>` loescht Nachrichten mit Fortschrittsanzeige (Admin). `/timeout <user> <dauer>` schaltet einen User temporaer stumm (Admin).

### 3.4 Monitor Bot Commands

`/performance` zeigt System-Performance: CPU, RAM, Disk (Spieler). `/dashboard` aktualisiert das Dashboard-Embed manuell (Admin). `/stats [spieler]` zeigt Satisfactory-Spieler-Statistiken (Spieler). `/report` generiert einen Satisfactory-Wochenbericht (Spieler). `/mcstats [spieler] [server]` zeigt Minecraft-Spieler-Statistiken (Spieler). `/mcreport [server]` generiert einen Minecraft-Wochenbericht (Spieler). `/mccrashlog [server]` zeigt die letzten Crash-Logs eines MC-Servers (Admin). `/scheduler` zeigt den Scheduler-Status und Konfiguration (Admin). `/update_check` sucht manuell nach Updates — SAT via SteamCMD, MC via Paper API (Admin).

---

## 4. Monitoring & Automatisierung

### Health Checks

**Satisfactory (alle 2 Minuten):** Prueft API-Erreichbarkeit und Prozess-Status. Bei Ausfall wird nach 3 aufeinanderfolgenden Fehlschlaegen (6 Minuten) eine Downtime-Benachrichtigung gesendet. Crash-Detection mit Auto-Restart (max 5 pro Stunde, 30s Wartezeit).

**Minecraft (alle 2 Minuten):** Prueft Prozess-Status via systemd und RCON-Erreichbarkeit. Nach 3 Fehlschlaegen (6 Minuten) wird eine Downtime-Benachrichtigung an den Admin-Channel gesendet. Bei Wiedererreichbarkeit folgt eine Recovery-Nachricht.

### Auto-Backup

**Satisfactory:** Alle 6 Stunden automatisches Savegame-Backup. Lokale Speicherung unter `/home/botuser/Discord_Bots/backups/`. Optionaler OneDrive-Upload via rclone.

**Minecraft:** Alle 6 Stunden automatisches World-Backup pro Server. Vor dem Backup wird `save-all` via RCON ausgefuehrt. Lokale Speicherung unter `/home/minecraft/backups/{server_id}/`. Automatisches Cleanup alter Backups. Optionaler OneDrive-Upload nach `MinecraftBackups/{server_id}/`.

### Daily Restart

Taeglicher Neustart um 04:00 Uhr fuer alle Server. Wird nur ausgefuehrt wenn der Server laenger als 12 Stunden laeuft. Wird uebersprungen wenn Spieler online sind. Bei Minecraft: In-Game-Warnungen 2 Minuten, 1 Minute und direkt vor dem Restart via RCON.

### Chat-Bridge

**Satisfactory:** API-basierter Chat-Relay. Nachrichten werden ueber die Satisfactory HTTP API abgerufen und an den konfigurierten Discord-Channel weitergeleitet. Discord-Nachrichten werden via API an den Server gesendet.

**Minecraft:** Log-Polling alle 5 Sekunden. Liest `latest.log` und erkennt via Regex: Chat-Nachrichten (`<Spieler> Nachricht`), Join/Leave Events, Advancements und Deaths. Nachrichten werden mit `AllowedMentions.none()` an Discord gesendet (Mention-Injection-Schutz). Discord→MC: Nachrichten werden via RCON `say [Discord] <User>: <Nachricht>` weitergeleitet, mit Rate-Limiting und RCON-Injection-Schutz.

### Player-Tracking

Separate PlayerTracker-Instanz pro Server (SAT + MC). Trackt Join/Leave-Events, berechnet Spielzeit pro Spieler, und generiert Wochenberichte. Daten werden in `data/` (SAT) bzw. `data/mc_{server_id}/` (MC) persistiert.

### Update-Checks

**Satisfactory:** Alle 6 Stunden via SteamCMD Build-ID Vergleich. Bei neuer Version Benachrichtigung im Admin-Channel.

**Minecraft Vanilla/Paper:** Alle 6 Stunden via Paper API. Vergleicht den aktuellen Build mit dem neuesten verfuegbaren Build. Nur fuer Vanilla/Paper aktiv, nicht fuer Better MC (Modpack-Updates sind manuell).

### Crash Replay

Bei Server-Crash werden die letzten Log-Zeilen analysiert und als Zusammenfassung im Admin-Channel gepostet. Unterstuetzt sowohl Satisfactory-Crashes als auch Minecraft-Crashes (separate Keyword-Listen fuer MC-typische Fehler wie OutOfMemoryError, ConcurrentModificationException etc.).

### Selftest

`/selftest` prueft alle Subsysteme: Bot-Konnektivitaet, Discord-Permissions, Satisfactory API, SAT-Prozess-Status, Backup-Pfade. Fuer jeden konfigurierten MC-Server zusaetzlich: Server-Status, RCON-Erreichbarkeit, Log-Pfad-Existenz, Backup-Pfad-Schreibrechte.

---

## 5. ENV-Variablen Referenz

### Discord (Pflicht)

`DISCORD_TOKEN_MANAGER` — GameServer Bot Token. `DISCORD_TOKEN_WATCHDOG` — Monitor Bot Token. `GUILD_ID` — Discord Server ID. `OWNER_ID` — Bot-Owner User ID. `ADMIN_ROLE_ID` — Admin-Rolle ID. `SATISFACTORY_ROLE_ID` — Spieler-Rolle ID. `ADMIN_LOG_CHANNEL_ID` — Admin-Log Channel. `PUBLIC_STATUS_CHANNEL_ID` — Oeffentlicher Status-Channel. `STATUS_EMBED_CHANNEL_ID` — Dashboard-Embed Channel.

### Discord (Optional)

`VOICE_STATS_CATEGORY_ID` — Voice-Channel Stats Kategorie. `NOTIFY_ROLE_ID` — Rolle fuer Benachrichtigungen. `MINECRAFT_ROLE_ID` — Rolle fuer MC-Commands (0 = deaktiviert).

### Satisfactory

`SATISFACTORY_SERVICE` — systemd Service-Name. `SATISFACTORY_USER` — Linux-User. `SATISFACTORY_SERVER_PATH` — Server-Installationspfad. `API_HOST`, `API_PORT`, `API_TOKEN` — API-Verbindung. `API_VERIFY_SSL` — SSL-Verifizierung (false fuer Self-Signed). `SATISFACTORY_SAVE_PATH` — Savegame-Pfad. `STEAMCMD_PATH` — SteamCMD-Pfad.

### Backup

`BACKUP_PATH` — Lokaler Backup-Ordner. `ONEDRIVE_ENABLED` — OneDrive-Backup aktivieren (true/false). `ONEDRIVE_REMOTE` — rclone Remote-Name. `ONEDRIVE_PATH` — Remote-Pfad.

### E-Mail (Optional)

`EMAIL_ENABLED` — E-Mail-Benachrichtigungen (true/false). `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` — SMTP-Konfiguration. `EMAIL_FROM`, `EMAIL_TO` — Absender und Empfaenger.

### Minecraft Multi-Server

Pro Server mit Prefix `MC_{SERVER_ID}_*`:

`MC_{ID}_SERVICE` — systemd Service-Name (Pflicht, aktiviert den Server). `MC_{ID}_DISPLAY_NAME` — Anzeigename in Discord. `MC_{ID}_PATH` — Server-Installationspfad. `MC_{ID}_WORLD_PATH` — World-Verzeichnis. `MC_{ID}_RCON_HOST` — RCON-Host (Standard: 127.0.0.1). `MC_{ID}_RCON_PORT` — RCON-Port. `MC_{ID}_RCON_PASSWORD` — RCON-Passwort. `MC_{ID}_BACKUP_PATH` — Backup-Verzeichnis. `MC_{ID}_LOG_PATH` — Pfad zu latest.log. `MC_{ID}_GAME_CHAT_CHANNEL_ID` — Discord Chat-Bridge Channel (0 = deaktiviert).

Aktuell konfigurierte Server-IDs: `BMC` (Better MC, Ports 25566/25575) und `VANILLA` (Paper, Ports 25565/25576).

---

## 6. Server-Infrastruktur

### Hardware

Netcup RS 4000 G12: 12 vCores, 32 GB RAM, ca. 950 GB freier Speicher. Ubuntu 22.04 LTS mit systemd. IP: 203.0.113.10, SSH-Port: 4422.

### Dienste und Ports

GameServer Bot (`gameserver-bot.service`) — kein externer Port. Monitor Bot (`monitor-bot.service`) — kein externer Port. Satisfactory (`satisfactory.service`) — Ports 7777, 15000, 15777 (UDP/TCP). MC Vanilla/Paper (`minecraft-vanilla.service`) — Port 25565 (Game, oeffentlich), Port 25576 (RCON, nur lokal). MC Better MC (`minecraft-bmc.service`) — Port 25566 (Game, oeffentlich), Port 25575 (RCON, nur lokal).

### RAM-Aufteilung

Satisfactory: variabel (bestehende Konfiguration). MC Vanilla/Paper: 2-4 GB (-Xms2G -Xmx4G), MemoryMax 6 GB. MC Better MC: 4-8 GB (-Xms4G -Xmx8G), MemoryMax 10 GB. Discord Bots: ca. 200-500 MB. System + Reserve: ca. 8 GB.

### systemd Services (Minecraft)

Beide MC-Services verwenden Aikar's G1GC JVM-Flags fuer optimale Garbage Collection. Graceful Shutdown via `rcon-cli stop` (ExecStop). Restart-on-failure mit maximal 3 Neustarts in 600 Sekunden. Resource-Limits (MemoryMax, CPUQuota=200%, LimitNOFILE=65536). User: minecraft, Group: minecraft.

### SSH-Zugang

`ssh netcup-marco` verbindet als marco (sudo-Befehle). `ssh netcup-botuser` verbindet als botuser (SCP-Uploads). Konfiguriert in `C:\Users\Marco\.ssh\config`, Port 4422.

### Deployment-Workflow

Code-Aenderungen werden lokal entwickelt und per SCP hochgeladen: `scp -P 4422 <dateien> botuser@203.0.113.10:/home/botuser/Discord_Bots/<pfad>`. Anschliessend Services neustarten: `sudo systemctl restart gameserver-bot.service monitor-bot.service`. Logs pruefen: `journalctl -u gameserver-bot -n 50 --no-pager`.

---

## 7. Sicherheit

### RCON-Injection-Schutz

Alle Nachrichten die via RCON an Minecraft gesendet werden, durchlaufen eine Sanitisierung. Sonderzeichen die RCON-Befehle manipulieren koennten, werden entfernt oder escaped.

### Mention-Injection-Schutz

Alle Nachrichten die von Minecraft nach Discord weitergeleitet werden, nutzen `AllowedMentions.none()`. Dadurch koennen Spieler im MC-Chat keine @everyone, @here oder Rollen-Mentions triggern.

### Path-Traversal-Schutz

Backup-Restore und -Delete Operationen validieren Pfade mit `.resolve()` und pruefen ob der aufgeloeste Pfad innerhalb des erlaubten Backup-Verzeichnisses liegt.

### Command-Injection-Prevention

systemctl-Aufrufe nutzen eine `ALLOWED_ACTIONS` Whitelist (frozenset). Nur definierte Aktionen wie start, stop, restart, status, is-active und show sind erlaubt.

### UFW/Player-IP-Tracking

Der Player-IP-Tracker kann IPs von Spielern via UFW blocken. IP-Adressen werden vor Verwendung mit Regex auf gueltiges IPv4-Format geprueft. Subprocess-Aufrufe nutzen `create_subprocess_exec()` statt Shell-Interpolation.

### Berechtigungssystem

Vierstufiges System: Owner (Bot-Besitzer, alle Rechte), Admin (Admin-Rolle, Server-Steuerung), Spieler (Spieler-Rolle, Info + Aktionen), Alle (nur lesende Befehle). Implementiert ueber `admin_only()` und `owner_only()` Decorators in `utils/permissions.py`.

---

## 8. Entwicklungshistorie

### v1.0.0 — Initiale Version

Grundlegendes 2-Bot-System fuer Satisfactory. Basis-Commands (start/stop/status), Health Check, einfache Backups.

### v2.0.0 — Feature-Erweiterung

Erweiterte Satisfactory-Features: Blueprints, Whitelist/Blacklist, Chat-Bridge, Savegame-Analyse, SteamCMD-Updates, OneDrive-Backup, E-Mail-Benachrichtigungen, Player-Tracking, Crash-Replay.

### v2.2.0 — Code-Review + Bugfixes

Umfassender Code-Review ueber 56 Dateien. 12 kritische Fehler behoben (Shell-Injection, Command-Injection, fehlende Imports, Async-Bugs). 8 Logik-Fehler behoben (Race Conditions, Nested Event Loops, Off-by-one). 52 Code-Qualitaet-Verbesserungen (Type Hints fuer 36 Dateien, 18 bare Exceptions, 13 Cog Error Handler). 6 Architektur-Optimierungen (sudoers Haertung, Watchdog-Service, Drop-Caches-Script).

### v3.0.0 — Minecraft-Integration (aktuell)

Komplette Minecraft Multi-Server Integration (Phase 14a-14o, 18 Commits). Neue Module: minecraft/server.py, minecraft/rcon.py, minecraft/backup.py, minecraft/chat_bridge.py, minecraft/settings_backup.py, minecraft/update_checker.py. Neue systemd Services: minecraft-vanilla.service, minecraft-bmc.service. MC-SAT Feature Parity: Stats-Tracker, Crash-Replay, Player-IP-Tracker, Update-Checker fuer MC. Code-Review: 3 Critical, 12 Warning, 1 Bug behoben. Deployment auf Server abgeschlossen, beide Bots laufen fehlerfrei.

---

## 9. Konfigurationsdateien

### config.json Struktur

Feature-Toggles, Intervalle und Schwellwerte. Jedes Feature kann einzeln aktiviert/deaktiviert werden. Minecraft-Scheduler-Konfiguration unter `scheduler.minecraft.*`: `daily_restart_hour` (Standard: 4), `auto_backup_interval_hours` (Standard: 6), `update_check_interval_hours` (Standard: 6).

### .env.example

Vollstaendige Vorlage mit allen ENV-Variablen und erklaerenden Kommentaren. Enthalt Satisfactory-, Discord-, Backup-, E-Mail- und Minecraft-Konfiguration. Liegt unter `config/.env.example`.

---

## 10. Offene Punkte / Naechste Schritte

### BMC Server-Setup (erfordert SSH)

Service-Datei Warnungen beheben (minecraft-bmc.service). Better MC Serverpack nach /home/minecraft/bettermc/ installieren. server.properties konfigurieren (Port 25566, RCON Port 25575). RCON-Passwoerter fuer beide MC-Server setzen (aktuell Platzhalter). BMC Server starten und testen.

### Discord-Channels (erfordert Discord)

Pro MC-Server einen Chat-Channel erstellen. Channel-IDs in config/.env eintragen (MC_BMC_GAME_CHAT_CHANNEL_ID und MC_VANILLA_GAME_CHAT_CHANNEL_ID). Bots neustarten damit ENV-Variablen geladen werden.

### Funktionstests (erfordert laufende Server)

`/mc status` — Status beider Server. `/mc start bmc` — BMC starten. `/selftest` — MC-Subsysteme pruefen. Chat-Bridge testen (Discord zu Minecraft und zurueck). `/mc backup create bmc` — Backup erstellen. Health-Check und Status-Embed verifizieren.
