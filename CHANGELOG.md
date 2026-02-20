# Changelog

Alle relevanten Aenderungen am Discord Bot System werden hier dokumentiert.

---

## [3.2.0] — 2026-02-20

### Hinzugefuegt

- **Phase 13: Web-Dashboard (F13)**
  - Vollstaendiges Admin-Dashboard mit FastAPI + HTMX + Jinja2
  - Discord OAuth2 Login mit Guild-/Rollen-Pruefung + Passwort-Fallback (bcrypt)
  - Dashboard-Uebersicht: Server-Kacheln, System-Performance, Bot-Status, Event-Feed
  - Server-Detail: Spielerliste, RCON-Konsole, Backups, Savegame-Info, Mods, Config, Analyse
  - Fehler-Uebersicht: Letzte ERROR/WARNING aus allen Bot-Logs
  - Admin Bot Setup: 10 Konfigurations-Tabs (Temp Voice, TS, WordFilter, AntiSpam, Warns, Reaction Roles, Leveling, Tickets, Audit, Giveaways)
  - Config-Panel: Feature-Toggles, Benachrichtigungs-Routing-Matrix, Login-Verwaltung, Bot-Profile
  - System-Seite: Echtzeit-Systeminfo (CPU, RAM, Disk) + Webmin-iframe-Einbettung
  - Stats Collector: Hintergrund-Task fuer Performance-Daten (5-Min-Intervall, Ringbuffer)
  - Analyse-API: REST-Endpunkte fuer Uptime, Performance, Spieler-Aktivitaet, Backup-Stats
  - 11 Python-Module (web/), 26 HTML-Templates, Dark-Theme

- **Phase 14: Command-Aufraeumung (F25)**
  - Server-Steuerung (start/stop/restart/cancel) aus Discord entfernt → Dashboard
  - Admin-Config-Commands (set, update, autosave, settings_backup/restore) entfernt → Dashboard
  - `/sat backup` umbenannt zu `/sat sav` (Savegame-Verwaltung)
  - `/server` und `/ping` entfernt (Dashboard zeigt alles)
  - Mod-Admin-Commands (install/uninstall/update/search/export/import) entfernt → Dashboard
  - Maintenance-Commands komplett ins Dashboard migriert
  - Hilfe-Uebersicht aktualisiert (rollenbasiert, nur verbleibende Commands)
  - ~2100 Zeilen Code entfernt, nur Lese-Commands bleiben in Discord

- **Phase 10-12: Diverse Verbesserungen**
  - MC IP-Ban wie SAT (UFW-Firewall, F23)
  - MC Ankuendigungs-Banner /mc say (F21)
  - MC Gameplay-Commands entfernt (F22: difficulty/weather/time/gamemode → nur In-Game)
  - SAT Auto-Update Verbesserung (F20: sofort bei leerem Server)

### Sicherheit

- XSS-Schutz: html.escape() fuer alle User-Inputs in HTMLResponse
- CSRF-Schutz: SameSite=lax Cookies + OAuth2 State-Token (session.pop)
- Exception-Leak-Prevention: Interne Fehlermeldungen nur im Server-Log
- Rate-Limiting: Max 5 Login-Versuche pro 15 Minuten
- JWT-Session: httpOnly Cookies, 24h Ablaufzeit
- Unused-Import-Cleanup in allen Web-Modulen

### Geaendert

- Satisfactory: backup_grp → sav_grp, config_load → sav_load, config_stats → sav_stats
- Minecraft: config_grp Beschreibung auf "nur Lesen" gesetzt
- Mod-Cog: Nur noch list + info Commands (Spieler-sichtbar)
- Maintenance-Cog: Leere Huelle (alle Commands ins Dashboard)
- .env.example: Neue Dashboard-Variablen (WEB_*, DISCORD_CLIENT_*, WEB_WEBMIN_URL)

---

## [3.1.0] — 2026-02-20

### Hinzugefuegt

- **Phase 8a: Server-Offline-Decorator**
  - `@server_online_required(server_attr)` Decorator fuer SAT-Commands
  - `_require_online_server()` Helper fuer MC Multi-Server-Commands
  - Wiederholendes `if not await srv.is_running(): return` Pattern refactored

- **Phase 8b: MC Autosave-Command**
  - `/mc config autosave <intervall> [server]` — Periodisches save-all via RCON
  - JSON-Persistenz in `data/mc_autosave.json`
  - cog_load/cog_unload Lifecycle fuer Task-Management

- **Phase 8c: Backup-Statistiken**
  - `/backup stats` — Detaillierte Backup-Statistiken pro Server
  - Disk-Usage mit Farbcodierung und Fortschrittsbalken
  - Async I/O via run_in_executor

- **Phase 8d: Config-Backup Rotation + Verschluesselung**
  - Konfigurierbare max_backups aus config.json
  - Optionale GPG-Verschluesselung (AES256, symmetrisch)
  - ENV: `GPG_PASSPHRASE`, config: `backup.config_encrypt`

- **Phase 8e: MC Blacklist-System**
  - Serveruebergreifendes Ban-System fuer Minecraft
  - `/mc blacklist add|remove|list|history`
  - Ban-Historie mit active/inactive Status
  - Automatische Blacklist-Integration bei `/mc players ban`
  - Mention-Injection-Schutz in allen Embeds

- **Phase 8f: Scheduled Messages**
  - `/schedule add|list|cancel` — Geplante Nachrichten
  - Relative/absolute Zeitangaben, Wiederholung (einmalig/taeglich/woechentlich)
  - Persistenz in `data/scheduled_messages.json`
  - Zeitzone: Europe/Berlin, max. 20 aktive Schedules

- **Phase 8g: Web-Status-Seite**
  - HTML-Generator mit Jinja2-Template (Dark-Mode, responsive)
  - Auto-Refresh alle 60s, Farbcodierung, System-Performance
  - Nginx-Setup-Script (`scripts/setup_nginx.sh`)
  - ENV: `WEB_STATUS_ENABLED`, `WEB_STATUS_PATH`

- **Phase 8h: BMC Modpack-Update-Check**
  - Modrinth/CurseForge API Unterstuetzung
  - Periodischer Check alle 12h mit Admin-Benachrichtigung
  - `/mc config modpack_check` fuer manuellen Check
  - ENV: `MC_BMC_MODPACK_ID`, `MC_BMC_MODPACK_VERSION`, `MC_BMC_MODPACK_SOURCE`

- **/clear Abbruchfunktion**
  - `/clear` ohne Parameter bricht laufenden Loeschvorgang ab
  - Cancel-Event-System fuer sauberen Abbruch

### Behoben (Phase 7 + Phase 9 Re-Review)

- 20 CRITICAL + 8 WARNING Fixes aus Komplett-Review (63 Dateien)
- Scheduled Messages: changed-Flag korrekt initialisiert
- Scheduled Messages: Nachrichtenlaenge auf 2000 Zeichen validiert

### Sicherheit

- Mention-Injection-Schutz in allen neuen Embeds (discord.utils.escape_mentions)
- RCON-Input-Sanitisierung fuer alle neuen Commands
- GPG-Verschluesselung fuer Config-Backups (optional)

---

## [3.0.0] — 2026-02-20

### Hinzugefuegt

- **Minecraft Multi-Server Integration (Phase 14a-14o)**
  - Unterstuetzung fuer 2 MC-Server: Better MC (BMC3 Fabric) + Vanilla/Paper
  - Prefix-basiertes ENV-System (`MC_{SERVER_ID}_*`) fuer beliebig viele Server
  - `modules/minecraft/server.py` — MinecraftServer-Klasse mit systemd-Steuerung, RCON, Uptime-Tracking
  - `modules/minecraft/rcon.py` — Async RCON-Client mit signed ints, Reconnect, Bounded Loops
  - `modules/minecraft/backup.py` — World-Backup-Manager mit async I/O und automatischem Cleanup
  - `modules/minecraft/chat_bridge.py` — Bidirektionale Chat-Bridge (Log-Polling + RCON)
  - `modules/minecraft/settings_backup.py` — server.properties Backup/Restore
  - `modules/minecraft/update_checker.py` — Paper API Update-Check (nur Vanilla)

- **Minecraft Slash Commands (~25 neue Commands)**
  - `/mc status/start/stop/restart/cancel` — Server-Steuerung mit Countdown und In-Game-Warnungen
  - `/mc players list/kick/ban` — Spieler-Verwaltung via RCON
  - `/mc backup create/list/restore` — World-Backup-Management
  - `/mc whitelist add/remove/list` — Whitelist-Verwaltung via RCON
  - `/mc command` — Direkte RCON-Befehlsausfuehrung (Owner)
  - `/mc say/difficulty/weather/time/gamemode` — Admin-Befehle
  - `/mc config settings/set/backup/restore/update/stats` — Konfiguration + World-Stats
  - `/mcstats`, `/mcreport`, `/mccrashlog` — MC-Statistiken und Crash-Logs (Monitor Bot)

- **Minecraft Monitoring (Monitor Bot)**
  - Health-Check alle 2 Minuten (Prozess + RCON), Downtime-Alerts nach 6 Minuten
  - Chat-Bridge: Log-Polling alle 5 Sekunden, Discord→MC via RCON
  - Auto-Backup alle 6 Stunden pro Server (mit save-all vor Backup)
  - Daily Restart um 04:00 (mit In-Game-Warnungen via RCON)
  - Player-Tracking: Separate Instanz pro MC-Server
  - Status-Dashboard: MC-Server-Status im bestehenden Embed
  - Update-Check via Paper API (alle 6 Stunden, nur Vanilla)
  - Crash-Replay mit MC-spezifischen Error-Keywords

- **MC-SAT Feature Parity (Commit 404a017)**
  - StatsTracker: Multi-Server Support (`server_type`, `server_id`)
  - CrashReplay: `game_type="mc"` mit MC-Error-Keywords
  - PlayerIPTracker: MC-Regex-Patterns fuer Login/Join/Leave
  - EmailNotifier: `server_label` Parameter
  - ConfigValidator: Erweiterte MC-ENV-Checks

- **systemd Service-Definitionen**
  - `minecraft-vanilla.service` — Paper MC mit Aikar-Flags, 2-4G RAM, RCON Graceful-Shutdown
  - `minecraft-bmc.service` — Better MC mit Aikar-Flags, 4-8G RAM, Resource-Limits
  - Setup-Scripte: `setup_minecraft.sh`, `setup_minecraft_fix.sh`

### Sicherheit

- Mention-Injection-Schutz: `AllowedMentions.none()` fuer alle MC→Discord Nachrichten
- RCON-Injection-Schutz: Sanitisierung aller Discord→MC Nachrichten
- Path-Traversal-Schutz: `.resolve()` + Prefix-Check in Backup restore/delete
- systemctl Action-Whitelist: `_ALLOWED_ACTIONS` frozenset
- Per-Server Timer-Keys (verhindert globale Blockierung)

### Behoben (Code-Review Phase 6)

- 3 Critical: Mention-Injection, globale Timer-Blockierung, Tasks ohne MC-Server
- 12 Warnings: Fehlende Instanzen, sync I/O, process_commands Blockierung, u.a.
- 1 Bug: `close_all_sessions()` leerte `_online_players` Set nicht (Endlos-Spam)

---

## [2.2.0] — 2026-02-18

### Behoben

- **12 kritische Fehler:** Shell-Injection in optimizer.py, Command-Injection in server.py, fehlende Imports (asyncio), Namespace-Iteration-Bug, fehlender await bei crash_replay.capture(), OWNER_ID ohne Default, Shutdown-Cleanup unvollstaendig
- **8 Logik-Fehler:** Nested Event Loop in maintenance.py, Race Condition in savegame_analyzer.py, Rate-Limiting Off-by-one in anti_spam.py, None-Safety in mod_manager.py, Path-Traversal in blueprint_manager.py
- **6 Architektur-Fixes:** sudoers Haertung (bash -c entfernt, Wildcards eingeschraenkt), bot-watchdog.service vervollstaendigt, drop-caches.sh Script erstellt

### Verbessert

- Type Hints fuer 36 Dateien vervollstaendigt (Python 3.9-kompatibel)
- 18 bare Exceptions durch spezifische Exceptions ersetzt
- 13 Cog Error Handler vereinheitlicht (CheckFailure → ephemeral, Logging mit exc_info)
- Dokumentation: REVIEW_BEFUNDE.md, REVIEW_OFFEN.md erstellt

---

## [2.0.0] — 2026-02

### Hinzugefuegt

- Satisfactory Blueprint-Management (Upload/Download/List/Delete)
- Whitelist/Blacklist-System
- Chat-Bridge (Satisfactory ↔ Discord)
- Word Filter + Anti-Spam
- Savegame-Analyse und -Statistiken
- SteamCMD Update-Checker
- OneDrive Cloud-Backup via rclone
- E-Mail-Benachrichtigungen
- Player-Tracking mit Wochenberichten
- Crash-Replay (Log-Analyse)
- Performance-Monitoring mit Schwellwert-Warnungen
- Voice-Channel Stats
- Command Audit-Logging

---

## [1.0.0] — 2026-01

### Hinzugefuegt

- Initiales 2-Bot-System (GameServer Bot + Monitor Bot)
- Satisfactory Server-Steuerung (start/stop/restart/status)
- Health Check mit Auto-Restart
- Einfaches Backup-System
- Dashboard-Embed
- Daily Restart (04:00)
